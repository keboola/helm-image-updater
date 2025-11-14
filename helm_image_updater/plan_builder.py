"""Plan builder - creates an execution plan from configuration."""

import os
import yaml
import logging
from typing import List, Dict, Any, Optional

from .models import UpdatePlan, FileChange, PRPlan, UpdateStrategy, TagChange, PRType
from .environment import EnvironmentConfig
from .io_layer import IOLayer
from .tag_classification import detect_tag_type, TagType
from .stack_classification import classify_stack, get_dev_stacks
from .message_generation import (
    generate_commit_message,
    generate_pr_title,
    generate_pr_title_prefix,
    format_pr_body_with_metadata,
)
from .config import CANARY_STACKS, IGNORED_FOLDERS, DEV_STACK_MAPPING
from .cloud_detection import get_stack_cloud_provider
from .grouping_strategies import GroupingStrategyHandler, GroupingContext, should_auto_merge

logger = logging.getLogger(__name__)


def prepare_plan(config: EnvironmentConfig, io_layer: IOLayer, env: Optional[Dict[str, str]] = None) -> UpdatePlan:
    """
    Prepare a complete execution plan.

    This function reads current state and determines all changes needed,
    but doesn't make any modifications.

    Args:
        config: Environment configuration
        io_layer: IO layer for file and git operations
        env: Optional environment variables dict (defaults to os.environ)
    """
    if env is None:
        env = os.environ
    # Determine strategy
    strategy = _determine_strategy(config)
    
    # Log strategy info
    if strategy == UpdateStrategy.DEV:
        logger.info("Updating dev stacks (dev- tag)")
    elif strategy == UpdateStrategy.PRODUCTION:
        logger.info("Updating all stacks (production- tag)")
    elif strategy == UpdateStrategy.CANARY:
        canary_prefix = config.image_tag.split('-')[1] if config.image_tag and '-' in config.image_tag else ""
        logger.info(f"Detected canary tag, switching to branch 'canary-{canary_prefix}'")
        io_layer.switch_branch(f"canary-{canary_prefix}")
        logger.info(f"Successfully switched to branch 'canary-{canary_prefix}'")
        logger.info("Updating canary stack")
    elif strategy == UpdateStrategy.OVERRIDE:
        logger.info(f"Override stack: {config.override_stack}")
    
    # Create base plan
    plan = UpdatePlan(
        strategy=strategy,
        helm_chart=config.helm_chart,
        image_tag=config.image_tag,
        extra_tags=config.extra_tags,
        dry_run=config.dry_run,
        multi_stage=config.multi_stage,
        override_stack=config.override_stack,
        metadata=config.metadata,
    )
    
    # Discover target stacks
    all_stacks = _discover_stacks(io_layer)
    plan.target_stacks = _select_target_stacks(all_stacks, strategy, config)
    
    if not plan.target_stacks:
        logger.warning(f"No stacks found for strategy {strategy.value}")
        return plan
    
    # Read current state and calculate changes
    stack_changes = _calculate_all_changes(plan, io_layer)
    
    if not stack_changes:
        raise RuntimeError(
            f"\nError: tag.yaml for chart {plan.helm_chart} does not exist in any stack or all tags are already up to date (noop change)."
        )
    
    # Create file changes
    for stack_change in stack_changes:
        plan.file_changes.append(stack_change['file_change'])
    
    # Group changes into PRs using the new strategy handler
    handler = GroupingStrategyHandler()
    context = GroupingContext(
        config=config,
        plan=plan,
        stack_changes=stack_changes,
        io_layer=io_layer,
        env=env
    )
    pr_groups = handler.group_changes(context)

    # Create PR plans
    for pr_group in pr_groups:
        pr_plan = _create_pr_plan(pr_group, plan, config, env)
        plan.pr_plans.append(pr_plan)
    
    return plan


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
    
    return UpdateStrategy.DEV  # Default


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
        canary_tag_prefix = f"canary-{config.image_tag.split('-')[1]}" if config.image_tag and len(config.image_tag.split('-')) > 1 else ""
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
                logger.warning(f"{tag_file_path} not found, skipping")
                continue

            current_data = yaml.safe_load(current_content)
        except Exception as e:
            logger.warning(f"Failed to read {tag_file_path}: {e}")
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
        
        # Apply changes to create new content
        new_data = _apply_changes_to_data(current_data, changes)
        new_content = yaml.dump(new_data, default_flow_style=False, sort_keys=False)
        
        # Create change description
        change_descriptions = []
        for change in changes:
            change_descriptions.append(
                f"{change.path} from {change.old_value} to {change.new_value}"
            )
        
        stack_changes.append({
            'stack': stack,
            'file_change': FileChange(
                file_path=tag_file_path,
                old_content=current_content,
                new_content=new_content,
                change_description=f"Updated {stack}/{plan.helm_chart}/tag.yaml: " + 
                                 ", ".join(change_descriptions)
            ),
            'changes': changes
        })
    
    return stack_changes

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


# Note: _group_changes_for_prs functionality is now handled by GroupingStrategyHandler


def _create_pr_plan(pr_group: Dict[str, Any], plan: UpdatePlan, config: EnvironmentConfig, env: Dict[str, str]) -> PRPlan:
    """Create a PR plan from a group of changes."""
    import random
    import string

    # Generate shortened branch name
    suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=4))

    # Parse pr_type from string to enum
    pr_type_str = pr_group['pr_type']
    try:
        pr_type = PRType(pr_type_str)
    except ValueError:
        logger.warning(f"Unknown pr_type '{pr_type_str}', defaulting to STANDARD")
        pr_type = PRType.STANDARD

    cloud_provider = pr_group.get('cloud_provider', '')

    # Create descriptive but short branch name based on PR type
    if pr_type in (PRType.MULTI_STAGE_DEV, PRType.MULTI_STAGE_PROD):
        # Multi-stage: dummy-service-prod-sync-gcp-production-tag-abc1
        stage = "dev" if pr_type == PRType.MULTI_STAGE_DEV else "prod"
        cloud_suffix = f"-{cloud_provider}" if cloud_provider else ""
        branch_name = f"{plan.helm_chart}-{stage}-sync{cloud_suffix}-{plan.image_tag}-{suffix}"
    elif pr_type == PRType.CANARY:
        # Canary: dummy-service-canary-canary-tag-abc1
        branch_name = f"{plan.helm_chart}-canary-{plan.image_tag}-{suffix}"
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
    
    # Generate PR title prefix
    pr_title_prefix = generate_pr_title_prefix(
        strategy=plan.strategy,
        is_multi_stage=plan.multi_stage,
        user_requested_automerge=config.automerge,
        target_stacks=pr_group['stacks'],
        cloud_provider=pr_group.get('cloud_provider')
    )
    
    # Generate PR title
    pr_title = generate_pr_title(
        pr_title_prefix=pr_title_prefix,
        helm_chart=plan.helm_chart,
        image_tag=plan.image_tag,
        extra_tags=plan.extra_tags,
        target_stacks=pr_group['stacks']
    )
    
    # Generate PR body
    pr_body = format_pr_body_with_metadata(
        helm_chart=plan.helm_chart,
        image_tag=plan.image_tag,
        metadata=plan.metadata
    )
    
    # Determine auto-merge using the new function
    auto_merge = should_auto_merge(pr_type, config.automerge, config.grouping_strategy)

    logger.info(f"🔀 Auto-merge decision for {pr_type.value}:")
    logger.info(f"   - pr_type: {pr_type.value}")
    logger.info(f"   - user_requested: {config.automerge}")
    logger.info(f"   - grouping_strategy: {config.grouping_strategy.value}")
    logger.info(f"   - decision: {'AUTO-MERGE' if auto_merge else 'MANUAL ONLY'}")

    # Get files to commit
    files_to_commit = [change['file_change'].file_path for change in pr_group['changes']]

    return PRPlan(
        branch_name=branch_name,
        pr_title=pr_title,
        pr_body=pr_body,
        base_branch=pr_group['base_branch'],
        auto_merge=auto_merge,
        files_to_commit=files_to_commit,
        commit_message=commit_message,
        pr_type=pr_type,
        cloud_provider=cloud_provider
    )


def _get_canary_base_branch(image_tag: str) -> str:
    """Get the base branch for a canary deployment."""
    if image_tag and image_tag.startswith("canary-"):
        canary_tag_prefix = f"canary-{image_tag.split('-')[1]}" if len(image_tag.split('-')) > 1 else ""
        for prefix, canary_config in CANARY_STACKS.items():
            if prefix == canary_tag_prefix:
                return canary_config["base_branch"]
    return "main"


# Note: _should_auto_merge functionality is now handled by should_auto_merge in grouping_strategies.py