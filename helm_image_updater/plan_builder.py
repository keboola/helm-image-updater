"""Plan builder - creates an execution plan from configuration."""

import os
from io import StringIO
from typing import List, Dict, Any, Optional

from ruamel.yaml import YAML, YAMLError

# Module-level ruamel.yaml instance for round-trip (format-preserving) operations
_ryaml = YAML()
_ryaml.preserve_quotes = True

from .models import UpdatePlan, FileChange, PRPlan, UpdateStrategy, TagChange, DeployStrategy
from .wave_planning import wave_label, deploy_label, resolve_wave
from .manifest import compute_instance_id, extract_instance_id, compute_rollback_instance_id
from .environment import EnvironmentConfig
from .io_layer import IOLayer
from .tag_classification import detect_tag_type, TagType
from .stack_classification import classify_stack, get_dev_stacks
from .message_generation import (
    build_tag_string,
    generate_commit_message,
    generate_pr_title,
    generate_pr_title_prefix,
    generate_rollback_pr_title,
    format_pr_body_with_metadata,
    wave_release_search_link,
    manual_release_search_link,
)
from .config import CANARY_STACKS, IGNORED_FOLDERS, GITHUB_REPO


def _is_promoter_managed_standard(config: EnvironmentConfig, plan: UpdatePlan) -> bool:
    """True iff this run is the promoter-managed `standard` 2-wave release (ST-4126):
    DEPLOY_STRATEGY resolves to standard -- which since ST-4159 includes the EMPTY
    default -- AND a `PRODUCTION` deploy. ONLY production is staged: a `dev-*` tag (DEV),
    CANARY, and OVERRIDE are orthogonal UpdateStrategy axes that keep their own single-PR
    handling and are NEVER promoter-managed (a dev push must stay a fast auto-merged
    deploy, not an unmerged wave PR the promoter has to merge -- Halama review). Keeping
    this gate in ONE place stops the grouping and the manifest/guard wiring from diverging."""
    return (
        config.deploy_strategy == DeployStrategy.STANDARD
        and plan.strategy == UpdateStrategy.PRODUCTION
    )


def _is_promoter_managed_manual_per_stack(config: EnvironmentConfig, plan: UpdatePlan) -> bool:
    """True iff this run is a promoter-managed `manual-per-stack` release (ST-4157): an
    explicit DEPLOY_STRATEGY=manual-per-stack AND a `PRODUCTION` deploy. Like the standard
    gate, ONLY production is managed — DEV/CANARY/OVERRIDE are orthogonal axes that keep
    their own handling and are never promoter-managed. One PR per prod stack, no waves."""
    return (
        config.deploy_strategy == DeployStrategy.MANUAL_PER_STACK
        and plan.strategy == UpdateStrategy.PRODUCTION
    )


def _is_promoter_managed_rollback(config: EnvironmentConfig, plan: UpdatePlan) -> bool:
    """True iff this run is a promoter-managed `rollback` release (ST-4277): DEPLOY_STRATEGY=
    rollback AND a PRODUCTION-classified target. Mirrors `_is_promoter_managed_standard` /
    `_is_promoter_managed_manual_per_stack` so the manifest/guard wiring in `prepare_plan`
    can never diverge from `_group_changes_for_prs`'s rollback branch, which independently
    hard-raises on a non-production target (B1 already validates this at the env layer;
    this is defense-in-depth, not the primary check)."""
    return (
        config.deploy_strategy == DeployStrategy.ROLLBACK
        and plan.strategy == UpdateStrategy.PRODUCTION
    )


def prepare_plan(config: EnvironmentConfig, io_layer: IOLayer) -> UpdatePlan:
    """
    Prepare a complete execution plan.
    
    This function reads current state and determines all changes needed,
    but doesn't make any modifications.
    """
    # Determine strategy
    strategy = _determine_strategy(config)
    
    # Print strategy info
    if strategy == UpdateStrategy.DEV:
        print("Updating dev stacks (dev- tag)")
    elif strategy == UpdateStrategy.PRODUCTION:
        print("Updating all stacks (production- tag)")
    elif strategy == UpdateStrategy.CANARY:
        canary_tag = _get_canary_tag_value(config)
        canary_prefix = canary_tag.split('-')[1] if canary_tag and '-' in canary_tag else ""
        print(f"Detected canary tag, switching to branch 'canary-{canary_prefix}'")
        io_layer.switch_branch(f"canary-{canary_prefix}")
        print(f"Successfully switched to branch 'canary-{canary_prefix}'")
        print("Updating canary stack")
    elif strategy == UpdateStrategy.OVERRIDE:
        print(f"Override stack: {config.override_stack}")
    
    # Create base plan
    plan = UpdatePlan(
        strategy=strategy,
        helm_chart=config.helm_chart,
        image_tag=config.image_tag,
        extra_tags=config.extra_tags,
        dry_run=config.dry_run,
        override_stack=config.override_stack,
        metadata=config.metadata,
    )
    
    # Discover target stacks
    all_stacks = _discover_stacks(io_layer)
    plan.target_stacks = _select_target_stacks(all_stacks, strategy, config)
    
    if not plan.target_stacks:
        print(f"No stacks found for strategy {strategy.value}")
        return plan
    
    # Read current state and calculate changes
    stack_changes = _calculate_all_changes(plan, io_layer)
    
    if not stack_changes:
        if config.deploy_strategy == DeployStrategy.ROLLBACK:
            # ST-4277 §2 boundary: a rollback whose target tag is already on every stack
            # has an EMPTY diff -- HIU cannot open a PR, so no rollback release exists and
            # nothing preempts. That is by design: a rollback is a deploy, not a pure
            # cancel. Cancelling queued releases without deploying anything is what
            # `promoter:abandon-release` is for -- surface that guidance here rather than
            # the generic noop message.
            raise RuntimeError(
                f"\nError: rollback found no stacks to update for chart {plan.helm_chart} -- "
                f"the target tag is already on every stack (zero diff). Rollback is a deploy, "
                f"not a pure cancel: to cancel queued releases without deploying anything, "
                f"label their anchor PRs `promoter:abandon-release`."
            )
        raise RuntimeError(
            f"\nError: tag.yaml for chart {plan.helm_chart} does not exist in any stack or all tags are already up to date (noop change)."
        )
    
    # Create file changes
    for stack_change in stack_changes:
        plan.file_changes.append(stack_change['file_change'])
        if 'override_change' in stack_change:
            plan.file_changes.append(stack_change['override_change'])
    
    # Group changes into PRs
    pr_groups = _group_changes_for_prs(stack_changes, plan, config, io_layer)

    # Promoter-managed modes (wave strategies + promoter-managed `standard` ST-4126 +
    # `manual-per-stack` ST-4157 + `rollback` ST-4277): derive the manifest identity,
    # then guard against a duplicate fan-out.
    promoter_managed = (
        config.deploy_strategy.is_wave
        or _is_promoter_managed_standard(config, plan)
        or _is_promoter_managed_manual_per_stack(config, plan)
        or _is_promoter_managed_rollback(config, plan)
    )
    if promoter_managed and pr_groups:
        plan.manifest_context = _build_manifest_context(plan, config)
        if not config.dry_run:
            _guard_release_not_already_open(plan.manifest_context["instance_id"], io_layer)

    # Create PR plans
    for pr_group in pr_groups:
        pr_plan = _create_pr_plan(pr_group, plan, config)
        plan.pr_plans.append(pr_plan)
    
    return plan


def _get_canary_tag_value(config: EnvironmentConfig) -> Optional[str]:
    """
    Find the canary tag value from either IMAGE_TAG or extra tags.

    Returns:
        The canary tag value if found, None otherwise
    """
    # Check main image tag
    if config.image_tag and detect_tag_type(config.image_tag) == TagType.CANARY:
        return config.image_tag

    # Check extra tags
    for extra_tag in config.extra_tags:
        tag_value = extra_tag.get("value", "")
        if detect_tag_type(tag_value) == TagType.CANARY:
            return tag_value

    return None


def _determine_strategy(config: EnvironmentConfig) -> UpdateStrategy:
    """Determine the update strategy."""
    if config.override_stack:
        return UpdateStrategy.OVERRIDE

    # Check main tag
    tag_type = detect_tag_type(config.image_tag) if config.image_tag else TagType.INVALID

    if tag_type == TagType.DEV:
        return UpdateStrategy.DEV
    elif tag_type in (TagType.PRODUCTION, TagType.SEMVER):
        return UpdateStrategy.PRODUCTION
    elif tag_type == TagType.CANARY:
        return UpdateStrategy.CANARY

    # Check extra tags
    for extra_tag in config.extra_tags:
        tag_type = detect_tag_type(extra_tag.get("value", ""))
        if tag_type == TagType.DEV:
            return UpdateStrategy.DEV
        elif tag_type in (TagType.PRODUCTION, TagType.SEMVER):
            return UpdateStrategy.PRODUCTION
        elif tag_type == TagType.CANARY:
            return UpdateStrategy.CANARY

    return UpdateStrategy.DEV  # Default


def _effective_tag_type(plan: UpdatePlan) -> TagType:
    """Most-cautious tag class across image_tag + extra tags (ST-4169).

    PRODUCTION/SEMVER dominate CANARY/DEV, so a mixed deploy (e.g. a dev image tag
    plus a production extra tag) is treated as production and is NOT auto-merged.
    SEMVER collapses into PRODUCTION (a semver tag is a production release).
    Returns INVALID when no usable tag is present (e.g. a `pr-test-*` override
    build) -- which is non-production-class and so eligible to auto-merge onto a
    non-prod stack.
    """
    values = [plan.image_tag] + [t.get("value", "") for t in (plan.extra_tags or [])]
    types = [detect_tag_type(v) for v in values if v and v.strip()]
    if any(t in (TagType.PRODUCTION, TagType.SEMVER) for t in types):
        return TagType.PRODUCTION
    if any(t == TagType.CANARY for t in types):
        return TagType.CANARY
    if any(t == TagType.DEV for t in types):
        return TagType.DEV
    return TagType.INVALID


def _discover_stacks(io_layer: IOLayer) -> List[str]:
    """Discover all available stacks."""
    stacks = []
    for item in os.listdir("."):
        if os.path.isdir(item) and item not in IGNORED_FOLDERS:
            stacks.append(item)
    return sorted(stacks)


def _select_target_stacks(
    all_stacks: List[str], 
    strategy: UpdateStrategy, 
    config: EnvironmentConfig
) -> List[str]:
    """Select which stacks to update based on strategy."""
    if strategy == UpdateStrategy.OVERRIDE:
        if config.override_stack in all_stacks:
            return [config.override_stack]
        return []
    
    if strategy == UpdateStrategy.DEV:
        return get_dev_stacks(all_stacks)
    
    if strategy == UpdateStrategy.PRODUCTION:
        # All non-canary stacks
        result = []
        for stack in all_stacks:
            classification = classify_stack(stack)
            if not classification.is_canary and not classification.is_excluded:
                result.append(stack)
        return result
    
    if strategy == UpdateStrategy.CANARY:
        # Find matching canary stack
        canary_tag = _get_canary_tag_value(config)
        canary_tag_prefix = f"canary-{canary_tag.split('-')[1]}" if canary_tag and len(canary_tag.split('-')) > 1 else ""
        for prefix, canary_config in CANARY_STACKS.items():
            if prefix == canary_tag_prefix:
                stack_name = canary_config["stack"]
                return [stack_name] if stack_name in all_stacks else []
        return []
    
    return []


def _calculate_all_changes(plan: UpdatePlan, io_layer: IOLayer) -> List[Dict[str, Any]]:
    """Calculate changes for all target stacks."""
    stack_changes = []
    
    for stack in plan.target_stacks:
        tag_file_path = f"{stack}/{plan.helm_chart}/tag.yaml"
        
        # Read current content
        try:
            current_content = io_layer.read_file(tag_file_path)
            if current_content is None:
                print(f"Warning: {tag_file_path} not found, skipping")
                continue

            current_data = _ryaml.load(current_content)
        except Exception as e:
            print(f"Warning: Failed to read {tag_file_path}: {e}")
            continue

        # Calculate changes
        changes = calculate_tag_changes(
            current_data=current_data,
            image_tag=plan.image_tag,
            extra_tags=plan.extra_tags,
            commit_sha=plan.metadata.get("commit_sha")
        )

        if not changes:
            continue

        # Apply changes to create new content (preserving formatting)
        new_data = _apply_changes_to_data(current_data, changes)
        stream = StringIO()
        _ryaml.dump(new_data, stream)
        new_content = stream.getvalue()
        
        # Create change description
        change_descriptions = []
        for change in changes:
            change_descriptions.append(
                f"{change.path} from {change.old_value} to {change.new_value}"
            )
        
        stack_change = {
            'stack': stack,
            'file_change': FileChange(
                file_path=tag_file_path,
                old_content=current_content,
                new_content=new_content,
                change_description=f"Updated {stack}/{plan.helm_chart}/tag.yaml: " +
                                 ", ".join(change_descriptions)
            ),
            'changes': changes
        }

        # Check for argocdApplication override in values.yaml (only for production releases)
        if plan.strategy == UpdateStrategy.PRODUCTION:
            override_change = _check_and_remove_override(stack, plan.helm_chart, io_layer)
            if override_change:
                stack_change['override_change'] = override_change

        stack_changes.append(stack_change)

    return stack_changes

def _check_and_remove_override(
    stack: str, helm_chart: str, io_layer: IOLayer
) -> Optional[FileChange]:
    """Check values.yaml for argocdApplication.appManifestsRevision and remove it if present.

    Returns a FileChange if an override was found and should be removed, None otherwise.
    """
    values_file_path = f"{stack}/{helm_chart}/values.yaml"
    try:
        values_content = io_layer.read_file(values_file_path)
        if values_content is None:
            return None
    except Exception as e:
        print(f"Warning: could not read {values_file_path}, skipping override check: {e}")
        return None

    try:
        values_data = _ryaml.load(values_content)
    except YAMLError as e:
        print(f"Warning: could not parse {values_file_path}, skipping override check: {e}")
        return None

    if not isinstance(values_data, dict):
        return None

    argo_app = values_data.get("argocdApplication")
    if not isinstance(argo_app, dict):
        return None

    revision = argo_app.get("appManifestsRevision")
    if not revision or revision == "main":
        return None

    del values_data["argocdApplication"]["appManifestsRevision"]

    # If argocdApplication is now empty, remove the entire block
    if not values_data["argocdApplication"]:
        del values_data["argocdApplication"]

    if values_data:
        stream = StringIO()
        _ryaml.dump(values_data, stream)
        new_content = stream.getvalue()
    else:
        new_content = ""

    print(f"Detected appManifestsRevision override ({revision}) in {values_file_path}, will remove it")

    return FileChange(
        file_path=values_file_path,
        old_content=values_content,
        new_content=new_content,
        change_description=f"Removed appManifestsRevision override ({revision}) from {values_file_path}",
    )


def calculate_tag_changes(
    current_data: Dict[str, Any],
    image_tag: str,
    extra_tags: Optional[List[Dict[str, str]]] = None,
    commit_sha: Optional[str] = None
) -> List[TagChange]:
    """
    Calculate what changes need to be made to a tag.yaml file.
    
    Pure function that determines changes without modifying data.
    
    Args:
        current_data: Current YAML data as dict
        image_tag: New image tag
        extra_tags: Optional extra tags to update
        commit_sha: Optional commit SHA to add
        
    Returns:
        List of TagChange objects describing changes to make
    """
    changes = []
    
    # Check main image tag
    if image_tag and image_tag.strip():
        current_tag = current_data.get("image", {}).get("tag", "")
        if current_tag != image_tag:
            changes.append(TagChange(
                path="image.tag",
                old_value=current_tag,
                new_value=image_tag,
                change_type="image_tag"
            ))
    
    # Check extra tags
    if extra_tags:
        for extra_tag in extra_tags:
            path = extra_tag["path"]
            new_value = extra_tag["value"]
            
            # Navigate the path to get current value
            current_value = current_data
            try:
                for part in path.split("."):
                    current_value = current_value.get(part, {})
                if isinstance(current_value, dict):
                    current_value = None
            except (AttributeError, TypeError):
                current_value = None
            
            if current_value != new_value:
                changes.append(TagChange(
                    path=path,
                    old_value=current_value,
                    new_value=new_value,
                    change_type="extra_tag"
                ))
    
    # Check commit SHA
    if commit_sha:
        current_sha = current_data.get("image", {}).get("commit_sha")
        if current_sha != commit_sha:
            changes.append(TagChange(
                path="image.commit_sha",
                old_value=current_sha,
                new_value=commit_sha,
                change_type="commit_sha"
            ))
    
    return changes


def _apply_changes_to_data(data: Dict[str, Any], changes: List[TagChange]) -> Dict[str, Any]:
    """Apply changes to the data structure."""
    import copy
    new_data = copy.deepcopy(data)
    
    for change in changes:
        # Navigate to the correct location and update
        path_parts = change.path.split('.')
        current = new_data
        for part in path_parts[:-1]:
            if part not in current:
                current[part] = {}
            current = current[part]
        current[path_parts[-1]] = change.new_value
    
    return new_data


def _group_changes_for_prs(
    stack_changes: List[Dict[str, Any]], 
    plan: UpdatePlan,
    config: EnvironmentConfig,
    io_layer: IOLayer
) -> List[Dict[str, Any]]:
    """Group changes into pull requests based on strategy."""

    # ST-4277 rollback: ONE wave-0 PR over every changed stack (fleet-converging),
    # never auto-merged; the promoter preempts older releases then merges it. This
    # branch is FIRST (before is_wave) and hard-raises on a non-production target --
    # B1's env-layer validation already rejects a non-production rollback, but this guard
    # makes a silent fall-through to the auto-merged DEV/OVERRIDE tail below IMPOSSIBLE
    # (a rollback that skips preemption must never exist).
    if config.deploy_strategy == DeployStrategy.ROLLBACK:
        if plan.strategy != UpdateStrategy.PRODUCTION:
            raise RuntimeError(
                f"rollback requires a production/semver-classified target (got {plan.strategy.value})"
            )
        return [{
            'stacks': [sc['stack'] for sc in stack_changes],
            'changes': stack_changes,
            'base_branch': 'main',
            'pr_type': 'wave',
            'wave_number': 0,
            'labels': [wave_label(0), deploy_label(config.deploy_strategy)],
        }]

    # Promoter-managed wave strategies: one PR per wave (0..3), unmerged, labeled.
    if config.deploy_strategy.is_wave:
        return _group_changes_by_wave(stack_changes, plan, config, io_layer)

    # Promoter-managed `standard`: 2-wave dev→prod release (ST-4126). See
    # `_is_promoter_managed_standard` — DEPLOY_STRATEGY=standard (the ST-4159 universal
    # default, so this covers the EMPTY strategy too) on a PRODUCTION deploy. CANARY and
    # OVERRIDE are orthogonal UpdateStrategy axes and keep their own handling below (the
    # same predicate gates the manifest/guard wiring in prepare_plan, so the two can't
    # diverge); a DEV tag also falls through to the single-PR tail (never staged).
    if _is_promoter_managed_standard(config, plan):
        return _group_changes_standard_2wave(stack_changes, plan, config, io_layer)

    # Promoter-managed `manual-per-stack` (ST-4157): one PR per prod stack, no waves.
    # Same gating discipline as standard — PRODUCTION only; CANARY/OVERRIDE fall through.
    if _is_promoter_managed_manual_per_stack(config, plan):
        return _group_changes_manual_per_stack(stack_changes, plan, config)

    if plan.strategy == UpdateStrategy.CANARY:
        # Canary: always one PR
        return [{
            'stacks': [sc['stack'] for sc in stack_changes],
            'changes': stack_changes,
            'base_branch': _get_canary_base_branch(config),
            'pr_type': 'canary'
        }]
    
    # PRODUCTION is unreachable here by construction (standard 2-wave is the ST-4159
    # default; wave / manual-per-stack are explicit) -- guard so no future regression
    # can ever route a production deploy into an auto-mergeable single PR.
    if plan.strategy == UpdateStrategy.PRODUCTION:
        raise RuntimeError(
            "PRODUCTION deploys must be promoter-managed "
            "(standard/gradual/critical/critical-manual-gate/manual-per-stack); "
            "the legacy grouping was removed in ST-4159."
        )

    # DEV / OVERRIDE (and any defensive single-change case): one auto-mergeable PR.
    return [{
        'stacks': [sc['stack'] for sc in stack_changes],
        'changes': stack_changes,
        'base_branch': 'main',
        'pr_type': 'standard'
    }]


def _build_manifest_context(plan: UpdatePlan, config: Optional[EnvironmentConfig] = None) -> Dict[str, Any]:
    """Compute the wave-0 manifest's identity fields from the plan + pipeline metadata.

    ST-4277: when `config.deploy_strategy == ROLLBACK`, the display_name/instance_id use
    the rollback-specific shape (`compute_rollback_instance_id`), which is deliberately
    DISTINCT from the ST-4190 `<app>-<signature>` id of the original release (a duplicate
    instanceId would deadlock the promoter's Conflicted guard). `config` is optional and
    defaults to the non-rollback shape so existing direct-`plan`-only callers/tests are
    unaffected.

    ALL strategies (rollback included) now also carry `image_tag`/`extra_tags` in the
    returned context, so the executor can forward them into the live manifest.
    """
    source = (plan.metadata or {}).get("source", {})
    source_sha = source.get("sha")
    source_pr = source.get("pr_url")
    source_pr_author = source.get("pr_author")

    is_rollback = config is not None and config.deploy_strategy == DeployStrategy.ROLLBACK
    if is_rollback:
        display_name = f"ROLLBACK {plan.helm_chart} → {plan.image_tag or '(extra-tags)'}"
        instance_id = compute_rollback_instance_id(
            plan.helm_chart, plan.image_tag, plan.extra_tags,
            os.getenv("GITHUB_RUN_ID", "local"),
        )
    else:
        display_name = f"{plan.helm_chart}@{plan.image_tag}"
        instance_id = compute_instance_id(plan.helm_chart, source_sha, plan.image_tag, plan.extra_tags)

    return {
        "app": plan.helm_chart,
        "instance_id": instance_id,
        "display_name": display_name,
        "source_sha": source_sha if (source_sha and str(source_sha).lower() != "unknown") else None,
        "source_pr": source_pr or None,
        "source_pr_author": source_pr_author or None,
        "image_tag": plan.image_tag,
        "extra_tags": plan.extra_tags,
    }


def _group_changes_by_wave(stack_changes, plan, config, io_layer):
    """Group changes into one PR per rollout wave (0..3) for promoter consumption."""
    deploy_lbl = deploy_label(config.deploy_strategy)

    # Never roll an e2e stack into a production wave (defensive — known e2e are also in EXCLUDED_STACKS).
    stack_changes = [sc for sc in stack_changes if not sc['stack'].endswith('-e2e')]

    by_wave = {}
    for sc in stack_changes:
        metadata = io_layer.read_yaml(f"{sc['stack']}/stack-metadata.yaml")
        wave = resolve_wave(sc['stack'], metadata)
        by_wave.setdefault(wave, []).append(sc)

    present = set(by_wave)
    required = {0, 1, 2, 3}
    if present != required:
        missing = sorted(required - present)
        raise RuntimeError(
            f"Wave deploy requires non-empty waves 0..3 (promoter needs a contiguous "
            f"release:wave:0..3); missing/empty waves: {missing}. "
            f"Check rollout_wave in stack-metadata.yaml across target stacks."
        )

    groups = []
    for wave in sorted(by_wave):
        changes = by_wave[wave]
        groups.append({
            'stacks': [sc['stack'] for sc in changes],
            'changes': changes,
            'base_branch': 'main',
            'pr_type': 'wave',
            'wave_number': wave,
            'labels': [wave_label(wave), deploy_lbl],
        })
    return groups


def _group_changes_standard_2wave(stack_changes, plan, config, io_layer):
    """Group a promoter-managed `standard` deploy into a 2-wave dev→prod release (ST-4126).

    wave 0 = all dev stacks (the anchor, carries the manifest), wave 1 = all prod stacks.
    The cloud dimension is collapsed entirely (no per-cloud split, no rollout_wave lookup).

    1-wave fallback: an app present in only one tier (no dev stacks, or no prod stacks)
    emits a single wave-0 PR (the promoter handles 1-wave releases count-agnostically).
    Wave numbers are contiguous-from-0 by construction.
    """
    deploy_lbl = deploy_label(config.deploy_strategy)

    # Never roll an e2e stack into a wave (defensive — known e2e are also in EXCLUDED_STACKS).
    stack_changes = [sc for sc in stack_changes if not sc['stack'].endswith('-e2e')]

    # Wave 0 = dev stacks; wave 1 = the POSITIVE `is_production` set (NOT `not is_dev`).
    # Using the positive predicate is defense-in-depth (Halama review): a canary/excluded/
    # ignored stack that somehow reaches here is dropped, never mis-binned into the prod
    # wave — so it can't bypass the dev gate the feature exists to enforce.
    dev_changes = [sc for sc in stack_changes if classify_stack(sc['stack']).is_dev]
    prod_changes = [sc for sc in stack_changes if classify_stack(sc['stack']).is_production]

    # Build (tier-changes) in dev→prod order, dropping empty tiers, then number the
    # surviving tiers contiguously from 0. With both tiers present: dev=0, prod=1.
    # With only one tier present: that tier becomes wave 0 (1-wave fallback).
    tiers = [t for t in (dev_changes, prod_changes) if t]

    groups = []
    for wave, changes in enumerate(tiers):
        groups.append({
            'stacks': [sc['stack'] for sc in changes],
            'changes': changes,
            'base_branch': 'main',
            'pr_type': 'wave',
            'wave_number': wave,
            'labels': [wave_label(wave), deploy_lbl],
        })
    return groups


def _group_changes_manual_per_stack(stack_changes, plan, config):
    """Group a promoter-managed `manual-per-stack` deploy into ONE PR per stack (ST-4157).

    No waves: each member PR carries `deploy:manual-per-stack` (the anchor gets `release:anchor`
    + the manifest at executor time, once PR numbers are known). Members are EVERY stack the
    production tag lands on -- BOTH dev and prod (a production tag deploys to dev stacks too;
    only production stacks are tag-restricted). Uses the POSITIVE `is_dev or is_production`
    predicate so canary / e2e / otherwise-unclassified stacks are dropped (mirrors the
    standard 2-wave defensive filtering).
    """
    deploy_lbl = deploy_label(config.deploy_strategy)  # deploy:manual-per-stack

    def _is_member(stack):
        # A member is a real deploy target — dev (DEV_STACK_MAPPING) or prod. classify_stack
        # is the single source of truth: is_production already excludes EXCLUDED_STACKS (the
        # e2e stacks), CANARY_STACKS and IGNORED_FOLDERS, and is_dev keys off DEV_STACK_MAPPING.
        # So e2e/canary are dropped via the canonical config — no brittle name-suffix heuristic;
        # a new e2e stack just needs to be in EXCLUDED_STACKS (Halama review). Classify once.
        c = classify_stack(stack)
        return c.is_dev or c.is_production

    members = [sc for sc in stack_changes if _is_member(sc['stack'])]

    return [
        {
            'stacks': [sc['stack']],
            'changes': [sc],
            'base_branch': 'main',
            'pr_type': 'manual',
            'labels': [deploy_lbl],
        }
        for sc in members
    ]


def _guard_release_not_already_open(instance_id: str, io_layer: IOLayer) -> None:
    """Fail loudly if an open release with this instanceId already exists (re-run safety).

    Grouping moved from the release:id label to the wave-0 body manifest, so we detect a
    duplicate by parsing the instanceId out of each OPEN release:wave:0 anchor PR body.
    A second fan-out for the same instanceId would give the promoter a duplicate release.
    """
    for number, body in io_layer.find_open_release_anchors():
        if extract_instance_id(body) == instance_id:
            raise RuntimeError(
                f"Release '{instance_id}' already has an open anchor PR #{number}. "
                f"Refusing to create duplicate wave PRs. Close/finish the existing release first."
            )


def _create_pr_plan(pr_group: Dict[str, Any], plan: UpdatePlan, config: EnvironmentConfig) -> PRPlan:
    """Create a PR plan from a group of changes."""
    import random
    import string
    
    # Generate shortened branch name
    suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=4))
    
    # Create descriptive but short branch name based on PR type
    pr_type = pr_group['pr_type']

    if pr_type == 'canary':
        # Canary: dummy-service-canary-canary-tag-abc1
        branch_name = f"{plan.helm_chart}-canary-{plan.image_tag}-{suffix}"
    elif pr_type == 'wave':
        wave = pr_group['wave_number']
        branch_name = f"{plan.helm_chart}-wave{wave}-{plan.image_tag}-{suffix}"
    elif pr_type == 'manual':
        # manual-per-stack: one PR per stack — name it after the stack.
        stack = pr_group['stacks'][0]
        branch_name = f"{plan.helm_chart}-manual-{stack}-{plan.image_tag}-{suffix}"
    else:
        # Standard: dummy-service-production-tag-abc1
        branch_name = f"{plan.helm_chart}-{plan.image_tag}-{suffix}"
    
    # Ensure it's not too long
    branch_name = branch_name[:100]
    
    # Generate commit message
    commit_message = generate_commit_message(
        helm_chart=plan.helm_chart,
        image_tag=plan.image_tag,
        extra_tags=plan.extra_tags,
        target_stacks=pr_group['stacks']
    )
    
    # Generate PR title
    if pr_type == 'wave':
        wave = pr_group['wave_number']
        if config.deploy_strategy == DeployStrategy.ROLLBACK:
            # ST-4277: a rollback gets its own title shape -- no "wave N" framing (a
            # rollback is always exactly wave 0) and a ⏪ marker so it reads as a
            # rollback at a glance, distinct from every other wave PR title.
            pr_title = generate_rollback_pr_title(plan.helm_chart, plan.image_tag)
        else:
            # The suffix is the same chart+tags string (incl. extra tags) the release
            # search link quotes — they must match or the search finds nothing (ST-4035).
            pr_title = (
                f"[{plan.helm_chart} {config.deploy_strategy.value} wave {wave}] "
                f"{build_tag_string(plan.helm_chart, plan.image_tag, plan.extra_tags)}"
            )
    elif pr_type == 'manual':
        stack = pr_group['stacks'][0]
        pr_title = (
            f"[{plan.helm_chart} manual-per-stack {stack}] "
            f"{build_tag_string(plan.helm_chart, plan.image_tag, plan.extra_tags)}"
        )
    else:
        pr_title_prefix = generate_pr_title_prefix(
            strategy=plan.strategy,
            target_stacks=pr_group['stacks'],
        )
        pr_title = generate_pr_title(
            pr_title_prefix=pr_title_prefix,
            helm_chart=plan.helm_chart,
            image_tag=plan.image_tag,
            extra_tags=plan.extra_tags,
            target_stacks=pr_group['stacks']
        )
    
    # Collect removed overrides for PR body
    removed_overrides = []
    for change in pr_group['changes']:
        if 'override_change' in change:
            removed_overrides.append({
                'stack': change['stack'],
                'description': change['override_change'].change_description,
            })

    # Generate PR body
    pr_body = format_pr_body_with_metadata(
        helm_chart=plan.helm_chart,
        image_tag=plan.image_tag,
        metadata=plan.metadata,
        removed_overrides=removed_overrides,
    )

    # ST-4277: rollback PR body gets an explicit reason line when the dispatcher supplied
    # metadata.source.reason. This key is read ONLY by the rollback template (RFC §2) --
    # a non-rollback PR must never render it even if present.
    if config.deploy_strategy == DeployStrategy.ROLLBACK:
        reason = (plan.metadata or {}).get("source", {}).get("reason")
        if reason:
            pr_body += f"\n\n**Reason:** {reason}"

    # Wave PRs: link a PR search that finds every wave PR of this release. The link
    # quotes the full chart+tags string (incl. extra tags), which every wave PR title
    # embeds verbatim (see build_tag_string above); no PR numbers needed.
    if pr_type == 'wave':
        search_link = wave_release_search_link(
            GITHUB_REPO, plan.helm_chart, plan.image_tag, plan.extra_tags
        )
        pr_body += f"\n\n### Release\n[All wave PRs of this release]({search_link})"
    elif pr_type == 'manual':
        # manual-per-stack members have no wave label; link a search by app + strategy
        # labels + the chart+tags phrase so every member PR (anchor incl.) carries a link
        # to the whole release.
        search_link = manual_release_search_link(
            GITHUB_REPO, plan.helm_chart, plan.image_tag, plan.extra_tags
        )
        pr_body += f"\n\n### Release\n[All member PRs of this manual-per-stack release]({search_link})"


    # Determine auto-merge (ST-4169: by tag class + target stacks, not the automerge flag)
    auto_merge = _should_auto_merge(plan, pr_group['pr_type'], pr_group['stacks'])

    print(f"🔀 Auto-merge decision for {pr_group['pr_type']}:")
    print(f"   - pr_type: {pr_group['pr_type']}")
    print(f"   - stacks: {pr_group['stacks']}")
    print(f"   - decision: {'AUTO-MERGE' if auto_merge else 'MANUAL ONLY'}")
    
    # Get files to commit (tag.yaml + any override values.yaml changes)
    files_to_commit = [change['file_change'].file_path for change in pr_group['changes']]
    for change in pr_group['changes']:
        if 'override_change' in change:
            files_to_commit.append(change['override_change'].file_path)

    return PRPlan(
        branch_name=branch_name,
        pr_title=pr_title,
        pr_body=pr_body,
        base_branch=pr_group['base_branch'],
        auto_merge=auto_merge,
        files_to_commit=files_to_commit,
        commit_message=commit_message,
        labels=pr_group.get('labels', []),
        wave_number=pr_group.get('wave_number'),
        manual_member=(pr_type == 'manual'),
    )


def _get_canary_base_branch(config: EnvironmentConfig) -> str:
    """Get the base branch for a canary deployment."""
    canary_tag = _get_canary_tag_value(config)
    if canary_tag and canary_tag.startswith("canary-"):
        canary_tag_prefix = f"canary-{canary_tag.split('-')[1]}" if len(canary_tag.split('-')) > 1 else ""
        for prefix, canary_config in CANARY_STACKS.items():
            if prefix == canary_tag_prefix:
                return canary_config["base_branch"]
    return "main"


def _should_auto_merge(plan: UpdatePlan, pr_type: str, stacks: List[str]) -> bool:
    """Auto-merge a non-production deploy that targets only non-production stacks (ST-4169).

    release-promoter owns wave + manual-per-stack PRs. A production-class image
    (production-/semver, or a mixed deploy carrying one) is promoter-managed and never
    auto-merged here. Everything else -- dev-/canary- tags AND unrecognized tags such as
    the `pr-test-*` images used for `override-stack` test deploys -- auto-merges, provided
    no stack in the PR is production.

    The stack check is defense-in-depth: validate() rejects a dev/canary tag on a prod
    stack, but if one ever slips through we still must not auto-merge it. `not is_production`
    covers dev + e2e + canary and fails safe for a new/unknown stack name.

    ST-4277: a `rollback` release ALSO hits this via `pr_type == 'wave'` (its grouping
    always emits pr_type='wave', wave_number=0) -- it is a production-class path where
    release-promoter (not HIU) owns the merge, exactly like every other wave/manual PR.
    """
    if pr_type in ('wave', 'manual'):
        print(f"    🧠 Auto-merge: FALSE (pr_type={pr_type}; release-promoter / human merges)")
        return False

    if _effective_tag_type(plan) == TagType.PRODUCTION:
        print(f"    🧠 Auto-merge: FALSE (production-class image; promoter-managed)")
        return False

    prod_stacks = [s for s in stacks if classify_stack(s).is_production]
    if prod_stacks:
        print(f"    🧠 Auto-merge: FALSE (PR touches production stack(s): {prod_stacks})")
        return False

    print(f"    🧠 Auto-merge: TRUE (non-production deploy to non-production stacks: {stacks})")
    return True