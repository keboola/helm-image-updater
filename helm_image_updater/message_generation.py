"""
Message Generation Module

Pure functions for generating commit messages, PR titles, and PR bodies.
This module contains no side effects - only text formatting logic.
"""

from typing import List, Dict, Optional, Any

from .stack_classification import classify_stack
from .models import UpdateStrategy
from .tag_classification import detect_tag_type, TagType
from .config import CANARY_STACKS


def generate_commit_message(
    helm_chart: str,
    image_tag: str,
    extra_tags: Optional[List[Dict[str, str]]],
    target_stacks: List[str]
) -> str:
    """
    Generate a commit message for the changes.
    
    Pure function that creates commit messages.
    
    Args:
        helm_chart: Name of the Helm chart
        image_tag: The image tag
        extra_tags: Optional list of extra tags
        target_stacks: List of target stacks
        
    Returns:
        Commit message string
    """
    # Build tag string representation
    image_tag_str = (f"@{image_tag}" if image_tag else "") + "".join(
        f" {tag['path']}@{tag['value']}" for tag in (extra_tags or [])
    )
    
    # Determine stack description
    if len(target_stacks) == 1:
        stack_desc = f"in {target_stacks[0]}"
    elif len(target_stacks) > 1:
        classifications = [classify_stack(stack) for stack in target_stacks]
        if all(c.is_dev for c in classifications):
            stack_desc = "in dev stacks"
        elif all(c.is_production for c in classifications):
            stack_desc = "in production stacks"
        else:
            stack_desc = "in all stacks"
    else:
        stack_desc = ""
    
    return f"Update {helm_chart} to {image_tag_str} {stack_desc}".strip()


def generate_pr_title_prefix(
    strategy: UpdateStrategy,
    is_multi_stage: bool,
    user_requested_automerge: bool,
    target_stacks: List[str]
) -> str:
    """
    Generate the PR title prefix based on the update context.
    
    Pure function that determines PR title prefix.
    
    Args:
        strategy: The update strategy being used
        is_multi_stage: Whether this is a multi-stage deployment
        user_requested_automerge: Original user automerge preference
        target_stacks: List of stacks being updated
        
    Returns:
        PR title prefix string
    """
    # Canary updates always use canary sync
    if strategy == UpdateStrategy.CANARY:
        return "[canary sync]"
    
    # Determine if this is a dev or production update
    is_dev_update = False
    if target_stacks:
        classifications = [classify_stack(stack) for stack in target_stacks]
        is_dev_update = all(c.is_dev for c in classifications)
    
    # Multi-stage mode has special prefixes
    if is_multi_stage:
        if is_dev_update:
            if user_requested_automerge:
                return "[multi-stage] [test sync]"
            else:
                return "[multi-stage] [test sync manual]"
        else:
            if user_requested_automerge:
                return "[multi-stage] [prod sync]"
            else:
                return "[multi-stage] [prod sync manual]"
    
    # Regular mode
    if is_dev_update:
        return "[test sync]"
    else:
        return "[prod sync]"


def generate_pr_title(
    pr_title_prefix: str,
    helm_chart: str,
    image_tag: str,
    extra_tags: Optional[List[Dict[str, str]]],
    target_stacks: List[str]
) -> str:
    """
    Generate a complete PR title.
    
    Args:
        pr_title_prefix: The prefix for the PR title
        helm_chart: Name of the Helm chart
        image_tag: The image tag
        extra_tags: Optional list of extra tags
        target_stacks: List of target stacks
        
    Returns:
        Complete PR title string
    """
    # Build tag string representation
    image_tag_str = (f"@{image_tag}" if image_tag else "") + "".join(
        f" {tag['path']}@{tag['value']}" for tag in (extra_tags or [])
    )
    
    # Determine stack description for title
    if len(target_stacks) == 1:
        stack_desc = f" in {target_stacks[0]}"
    elif len(target_stacks) > 1:
        classifications = [classify_stack(stack) for stack in target_stacks]
        if all(c.is_dev for c in classifications):
            stack_desc = " in dev stacks"
        elif all(c.is_production for c in classifications):
            # For production stacks in multi-stage, be explicit
            stack_desc = ""  # The prefix already indicates this
        else:
            stack_desc = ""
    else:
        stack_desc = ""
    
    return f"{pr_title_prefix} {helm_chart}{image_tag_str}{stack_desc}".strip()


def format_pr_body_with_metadata(
    helm_chart: str,
    image_tag: str,
    metadata: Dict[str, Any]
) -> str:
    """
    Generate PR body when pipeline metadata is available.
    
    Pure function that formats PR body with metadata.
    
    Args:
        helm_chart: Name of the Helm chart
        image_tag: The image tag
        metadata: Pipeline trigger metadata
        
    Returns:
        Formatted PR body string
    """
    source = metadata.get("source", {})
    repo = source.get("repository", "")
    repo_url = source.get("repository_url", "")
    workflow_url = source.get("workflow_url", "")
    sha = source.get("sha", "Unknown")
    pr_url = source.get("pr_url", "")
    
    # Build PR line for insertion only if it exists
    pr_line = f"- **Pull Request:** [{pr_url}]({pr_url})\n" if pr_url else ""
    
    trigger_info = (
        "### 🔄 Pipeline Trigger\n"
        "#### Source Details\n"
        f"- **Repository:** [{repo}]({repo_url})\n"
        f"{pr_line}"
        f"- **Commit:** [`{sha[:7]}`]({repo_url}/commit/{sha})\n"
        f"- **Actor:** {source.get('actor', 'Unknown')}\n\n"
        "#### Workflow Information\n"
        f"- **Workflow:** [View Run]({workflow_url})\n"
        f"- **Timestamp:** {source.get('timestamp', 'Unknown')}"
    )
    
    change_summary = (
        "### 📝 Changes\n"
        f"- **Chart:** `{helm_chart}`\n"
        f"- **New Tag:** `{image_tag}`"
    )
    
    monitoring_links = (
        "### 📊 Monitoring\n"
        f"- [🎯 Argo CD](https://argo.keboola.tech/applications?labels=app%253D{helm_chart})\n"
        f"- [🐕 Datadog](https://app.datadoghq.eu/ci/deployments?search=%40deployment.service%3A{helm_chart})"
    )
    
    return (
        f"## 🤖 Automated Image Tag Update\n\n"
        f"{trigger_info}\n\n"
        f"{change_summary}\n\n"
        f"{monitoring_links}"
    )


