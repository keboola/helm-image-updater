"""Plan builder - creates an execution plan from configuration."""

import os
import yaml
from typing import List, Dict, Any, Optional

from .models import UpdatePlan, FileChange, PRPlan, UpdateStrategy, TagChange
from .environment import EnvironmentConfig
from .io_layer import IOLayer
from .tag_classification import detect_tag_type, TagType
from .stack_classification import classify_stack, filter_stacks_by_type
from .message_generation import (
    generate_commit_message,
    generate_pr_title,
    generate_pr_title_prefix,
    format_pr_body_with_metadata,
)
from .config import CANARY_STACKS, IGNORED_FOLDERS


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
        canary_prefix = config.image_tag.split('-')[1] if config.image_tag and '-' in config.image_tag else ""
        print(f"Detected canary tag, switching to branch 'canary-{canary_prefix}'")
        io_layer.switch_branch(f"canary-{canary_prefix}")
        print(f"Successfully switched to branch 'canary-{canary_prefix}'")
        print("Updating canary stack")
    elif strategy == UpdateStrategy.OVERRIDE:
        print(f"Override mode: targeting specific stack {config.override_stack}")
    
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
        print(f"No stacks found for strategy {strategy.value}")
        return plan
    
    # Read current state and calculate changes
    stack_changes = _calculate_all_changes(plan, io_layer)
    
    if not stack_changes:
        print("No changes needed.")
        return plan
    
    # Create file changes
    for stack_change in stack_changes:
        plan.file_changes.append(stack_change['file_change'])
    
    # Group changes into PRs
    pr_groups = _group_changes_for_prs(stack_changes, plan, config)
    
    # Create PR plans
    for pr_group in pr_groups:
        pr_plan = _create_pr_plan(pr_group, plan, config)
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
        return filter_stacks_by_type(all_stacks, "dev")
    
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
        canary_prefix = config.image_tag.split('-')[1] if config.image_tag else ""
        for prefix, stack in CANARY_STACKS.items():
            if prefix == canary_prefix:
                return [stack] if stack in all_stacks else []
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
                
            current_data = yaml.safe_load(current_content)
        except Exception as e:
            print(f"Warning: Failed to read {tag_file_path}: {e}")
            continue
        
        # Calculate changes
        changes = _calculate_tag_changes(
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

def _calculate_tag_changes(
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


def _apply_changes_to_data(data: Dict[str, Any], changes: List[Any]) -> Dict[str, Any]:
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
    config: EnvironmentConfig
) -> List[Dict[str, Any]]:
    """Group changes into pull requests based on strategy."""
    
    if plan.strategy == UpdateStrategy.CANARY:
        # Canary: always one PR
        return [{
            'stacks': [sc['stack'] for sc in stack_changes],
            'changes': stack_changes,
            'base_branch': _get_canary_base_branch(plan.image_tag),
            'pr_type': 'canary'
        }]
    
    if plan.multi_stage and plan.strategy == UpdateStrategy.PRODUCTION:
        # Multi-stage: separate dev and prod PRs
        dev_changes = [sc for sc in stack_changes if classify_stack(sc['stack']).is_dev]
        prod_changes = [sc for sc in stack_changes if classify_stack(sc['stack']).is_production]
        
        groups = []
        if dev_changes:
            groups.append({
                'stacks': [sc['stack'] for sc in dev_changes],
                'changes': dev_changes,
                'base_branch': 'main',
                'pr_type': 'multi_stage_dev'
            })
        if prod_changes:
            groups.append({
                'stacks': [sc['stack'] for sc in prod_changes],
                'changes': prod_changes,
                'base_branch': 'main',
                'pr_type': 'multi_stage_prod'
            })
        return groups
    
    # Default: one PR per stack or all in one
    if len(stack_changes) == 1 or plan.strategy in (UpdateStrategy.DEV, UpdateStrategy.OVERRIDE):
        # Single stack or dev: one PR
        return [{
            'stacks': [sc['stack'] for sc in stack_changes],
            'changes': stack_changes,
            'base_branch': 'main',
            'pr_type': 'standard'
        }]
    
    # Production without multi-stage: based on automerge
    if config.automerge:
        # One PR for all
        return [{
            'stacks': [sc['stack'] for sc in stack_changes],
            'changes': stack_changes,
            'base_branch': 'main',
            'pr_type': 'standard'
        }]
    else:
        # One PR per stack
        return [
            {
                'stacks': [sc['stack']],
                'changes': [sc],
                'base_branch': 'main',
                'pr_type': 'standard'
            }
            for sc in stack_changes
        ]


def _create_pr_plan(pr_group: Dict[str, Any], plan: UpdatePlan, config: EnvironmentConfig) -> PRPlan:
    """Create a PR plan from a group of changes."""
    import random
    import string
    
    # Generate branch name
    suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=4))
    stack_part = '-'.join(pr_group['stacks'][:2]) if len(pr_group['stacks']) <= 2 else f"{pr_group['stacks'][0]}-and-{len(pr_group['stacks'])-1}-more"
    branch_name = f"{plan.helm_chart}-{stack_part}-{plan.image_tag}-{suffix}"[:100]
    
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
        target_stacks=pr_group['stacks']
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
    
    # Determine auto-merge
    auto_merge = _should_auto_merge(plan, pr_group['pr_type'], config.automerge)
    
    # Get files to commit
    files_to_commit = [change['file_change'].file_path for change in pr_group['changes']]
    
    return PRPlan(
        branch_name=branch_name,
        pr_title=pr_title,
        pr_body=pr_body,
        base_branch=pr_group['base_branch'],
        auto_merge=auto_merge,
        files_to_commit=files_to_commit,
        commit_message=commit_message
    )


def _get_canary_base_branch(image_tag: str) -> str:
    """Get the base branch for a canary deployment."""
    if image_tag and image_tag.startswith("canary-"):
        parts = image_tag.split('-')
        if len(parts) >= 2:
            return f"canary-{parts[1]}"
    return "main"


def _should_auto_merge(plan: UpdatePlan, pr_type: str, user_requested: bool) -> bool:
    """Determine if a PR should be auto-merged."""
    if plan.strategy == UpdateStrategy.CANARY:
        return True  # Always auto-merge canary
    
    if pr_type == 'multi_stage_prod':
        return False  # Never auto-merge multi-stage production
    
    return user_requested