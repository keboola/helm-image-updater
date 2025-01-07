"""
Tag Update Module for Helm Image Updater

This module handles the core functionality of updating image tags in Helm charts.
It provides functions to update tag.yaml files across different stacks and
manages the creation of branches and pull requests for these updates.

Functions:
    update_tag_yaml: Updates a single tag.yaml file with new image tags
    update_dev_stack: Handles updates for development stacks
    update_production_stacks: Handles updates for production stacks
    handle_dev_tag: Orchestrates the dev tag update process
    handle_production_tag: Orchestrates the production tag update process

The module supports both individual stack updates and batch updates,
with different strategies for dev and production environments.
"""

import os
from pathlib import Path
import yaml
import dpath
from .config import UpdateConfig, DEV_STACKS, GITHUB_BRANCH, IGNORED_FOLDERS
from .pr_manager import create_pr
from .utils import get_trigger_metadata, random_suffix


def update_tag_yaml(
    stack_folder,
    helm_chart,
    image_tag,
    extra_tags=None,
    dry_run=False,
    commit_sha=False,
):
    """Update the tag.yaml file with the new image tag and optional additional tags.

    Args:
        stack_folder (str): The folder of the stack to update.
        helm_chart (str): The name of the Helm chart.
        image_tag (str): The new image tag to set.
        extra_tags (list): Optional list of dicts with 'path' and 'value' keys for additional tags.
                          Example: [{'path': 'agent.image.tag', 'value': 'v1.2.3'}]
        dry_run (bool): Whether to perform a dry run without making actual changes.
        commit_sha (bool): Whether to store commit SHA from metadata in tag.yaml.

    Returns:
        bool or None: True if updated, False if unchanged, None if file is missing.
    """
    tag_file = Path(stack_folder) / helm_chart / "tag.yaml"
    if not tag_file.exists():
        return None  # Return None for missing tag.yaml files

    with tag_file.open() as f:
        data = yaml.safe_load(f)

    changes_made = False

    # Update the default image tag
    old_tag = data["image"]["tag"]
    if image_tag.strip() and old_tag != image_tag:
        if not dry_run:
            dpath.set(data, "image.tag", image_tag, separator=".")
        print(
            f"{'Would update' if dry_run else 'Updated'} {stack_folder}/{helm_chart}/tag.yaml from {old_tag} to {image_tag}"
        )
        changes_made = True

    # Add commit SHA if enabled and metadata is available
    if commit_sha:
        metadata = get_trigger_metadata()
        if metadata:
            source = metadata.get("source", {})
            if sha := source.get("sha"):
                if not dry_run:
                    dpath.new(data, "image.commit_sha", sha, separator=".")
                print(
                    f"{'Would add' if dry_run else 'Added'} commit SHA {sha} to {stack_folder}/{helm_chart}/tag.yaml"
                )
                changes_made = True

    # Process additional tags if provided
    if extra_tags:
        for extra_tag in extra_tags:
            path = extra_tag["path"]
            try:
                old_value = dpath.get(data, path, separator=".")
            except KeyError:
                old_value = None

            if old_value != extra_tag["value"]:
                if not dry_run:
                    dpath.new(data, path, extra_tag["value"], separator=".")
                print(
                    f"{'Would update' if dry_run else 'Updated'} {extra_tag['path']} from {old_value} to {extra_tag['value']}"
                )
                changes_made = True

    if changes_made and not dry_run:
        with tag_file.open("w") as f:
            yaml.dump(data, f)

    return changes_made


def update_dev_stack(config: UpdateConfig):
    """Update all dev stacks with the new image tag.

    Args:
        config (UpdateConfig): The configuration object.

    Returns:
        tuple: A tuple containing a list of changes and a list of missing tag.yaml files.
    """
    print("Updating dev stacks (dev- tag)")
    changes = []
    missing_tags = []
    if not config.automerge:
        for dev_stack in DEV_STACKS:
            result = update_stack(
                config,
                dev_stack,
            )
            if result:
                changes.append(result)
    else:
        branch_name = (
            f"{config.helm_chart}-dev-stacks-{config.image_tag}-{random_suffix()}"
        )
        if not config.dry_run:
            config.repo.git.checkout("-b", branch_name)
        changes_made = False
        for dev_stack in DEV_STACKS:
            tag_file_path = f"{dev_stack}/{config.helm_chart}/tag.yaml"
            if update_tag_yaml(
                dev_stack,
                config.helm_chart,
                config.image_tag,
                extra_tags=config.extra_tags,
                dry_run=config.dry_run,
                commit_sha=config.commit_sha,
            ):
                if not config.dry_run:
                    config.repo.git.add(tag_file_path)
                changes_made = True
                changes.append(
                    {
                        "stack": dev_stack,
                        "chart": config.helm_chart,
                        "tag": config.image_tag,
                        "automerge": True,
                    }
                )
        if changes_made:
            image_tag_str = (
                f"@{config.image_tag}" if config.image_tag else ""
            ) + "".join(
                f" {tag['path']}@{tag['value']}" for tag in (config.extra_tags or [])
            )
            if not config.dry_run:
                config.repo.git.commit(
                    "-m",
                    f"Update {config.helm_chart} to {image_tag_str} in dev stacks",
                )
            pr_title_prefix = (
                "[multi-stage] [test sync]" if config.multi_stage else "[test sync]"
            )
            create_pr(
                config,
                branch_name,
                f"{pr_title_prefix} {config.helm_chart}{image_tag_str} in dev stacks",
            )
        else:
            print("No changes needed for dev stacks.")

    return changes, missing_tags


def update_production_stacks(config: UpdateConfig):
    """Update production stacks.

    Args:
        config (UpdateConfig): The configuration object.

    Returns:
        tuple: A tuple containing a list of changes and a list of missing tag.yaml files.
    """
    print("Updating all stacks (production- tag)")
    if not config.automerge:
        return update_all_stacks_separately(config)

    branch_name = f"{config.helm_chart}-all-stacks-{config.image_tag}-{random_suffix()}"
    if not config.dry_run:
        config.repo.git.checkout("-b", branch_name)

    changes = []
    missing_tags = []
    changes_made = False
    for stack in os.listdir("."):
        if os.path.isdir(stack):
            tag_file_path = f"{stack}/{config.helm_chart}/tag.yaml"
            if update_tag_yaml(
                stack,
                config.helm_chart,
                config.image_tag,
                extra_tags=config.extra_tags,
                dry_run=config.dry_run,
                commit_sha=config.commit_sha,
            ):
                if not config.dry_run:
                    config.repo.git.add(tag_file_path)
                changes_made = True
                changes.append(
                    {
                        "stack": stack,
                        "chart": config.helm_chart,
                        "tag": config.image_tag,
                        "automerge": config.automerge,
                    }
                )

    if changes_made:
        image_tag_str = (f"@{config.image_tag}" if config.image_tag else "") + "".join(
            f" {tag['path']}@{tag['value']}" for tag in (config.extra_tags or [])
        )
        if not config.dry_run:
            config.repo.git.commit(
                "-m",
                f"Update {config.helm_chart} to {image_tag_str} in all stacks",
            )
        pr_title_prefix = (
            "[multi-stage] [prod sync]" if config.multi_stage else "[prod sync]"
        )
        create_pr(
            config,
            branch_name,
            f"{pr_title_prefix} {config.helm_chart}{image_tag_str}",
        )
    else:
        print("No changes needed for production stacks.")

    return changes, missing_tags


def update_stack(config: UpdateConfig, stack_folder: str):
    """Update a single stack with the new image tag."""
    if not config.dry_run:
        config.repo.git.checkout(GITHUB_BRANCH)
        config.repo.git.pull("origin", GITHUB_BRANCH)  # Pull from main branch

    branch_name = (
        f"{config.helm_chart}-{stack_folder}-{config.image_tag}-{random_suffix()}"
    )
    if not config.dry_run:
        config.repo.git.checkout("-b", branch_name)

    tag_file_path = f"{stack_folder}/{config.helm_chart}/tag.yaml"
    if update_tag_yaml(
        stack_folder,
        config.helm_chart,
        config.image_tag,
        extra_tags=config.extra_tags,
        dry_run=config.dry_run,
        commit_sha=config.commit_sha,
    ):
        if not config.dry_run:
            config.repo.git.add(tag_file_path)
            config.repo.git.commit(
                "-m",
                f"Update {config.helm_chart} to {config.image_tag} in {stack_folder}",
            )
            pr_title_prefix = (
                "[multi-stage] [test sync]" if config.multi_stage else "[test sync]"
            )
            create_pr(
                config,
                branch_name,
                f"{pr_title_prefix} {config.helm_chart}@{config.image_tag} in {stack_folder}",
            )
        return {
            "stack": stack_folder,
            "chart": config.helm_chart,
            "tag": config.image_tag,
            "automerge": config.automerge,
        }
    return None


def update_all_stacks_separately(config: UpdateConfig):
    """Update all stacks individually."""
    changes = []
    missing_tags = []
    for stack in os.listdir("."):
        if os.path.isdir(stack):
            result = update_stack(config, stack)
            if result:
                changes.append(result)
    return changes, missing_tags


def update_all_stacks_single_pr(config: UpdateConfig, exclude_stacks: list = None):
    """Update all stacks in a single PR."""
    changes = []
    missing_tags = []
    branch_name = f"{config.helm_chart}-all-stacks-{config.image_tag}-{random_suffix()}"

    if not config.dry_run:
        config.repo.git.checkout(GITHUB_BRANCH)
        config.repo.git.pull("origin", GITHUB_BRANCH)  # Pull from main branch
        config.repo.git.checkout("-b", branch_name)

    exclude_stacks = exclude_stacks or []
    changes_made = False
    for stack in os.listdir("."):
        if (
            os.path.isdir(stack)
            and stack not in IGNORED_FOLDERS
            and stack not in exclude_stacks
        ):
            result = update_tag_yaml(
                stack,
                config.helm_chart,
                config.image_tag,
                extra_tags=config.extra_tags,
                dry_run=config.dry_run,
                commit_sha=config.commit_sha,
            )
            if result is None:
                missing_tags.append(stack)
            elif result:
                changes.append(
                    {
                        "stack": stack,
                        "chart": config.helm_chart,
                        "tag": config.image_tag,
                        "automerge": config.automerge,
                    }
                )
                changes_made = True
                if not config.dry_run:
                    config.repo.git.add(f"{stack}/{config.helm_chart}/tag.yaml")

    if changes_made:
        image_tag_str = (f"@{config.image_tag}" if config.image_tag else "") + "".join(
            f" {tag['path']}@{tag['value']}" for tag in (config.extra_tags or [])
        )
        if not config.dry_run:
            config.repo.git.commit(
                "-m",
                f"Update {config.helm_chart} to {image_tag_str} in {'production stacks' if exclude_stacks else 'all stacks'}",
            )
        pr_title_prefix = (
            "[multi-stage] [prod sync]" if config.multi_stage else "[prod sync]"
        )
        create_pr(
            config,
            branch_name,
            f"{pr_title_prefix} {config.helm_chart}{image_tag_str}{' in production stacks' if exclude_stacks else ''}".strip(),
        )
    else:
        print(
            f"No changes needed for {'production stacks' if exclude_stacks else 'all stacks'}"
        )

    return changes, missing_tags


def handle_dev_tag(config: UpdateConfig):
    """Handle dev tag updates."""
    return update_dev_stack(config)


def handle_production_tag(config: UpdateConfig):
    """Handle production tag updates.

    When multi_stage is True:
    - Creates and auto-merges a PR for dev stacks
    - Creates a single PR (without auto-merge) for production stacks

    When multi_stage is False:
    - Follows standard production stack update logic based on automerge setting
    """
    if not config.multi_stage:
        return update_production_stacks(config)

    # First update dev stacks with auto-merge (always auto-merge in multi-stage)
    dev_config = UpdateConfig(
        repo=config.repo,
        github_repo=config.github_repo,
        helm_chart=config.helm_chart,
        image_tag=config.image_tag,
        automerge=True,  # Always auto-merge dev in multi-stage
        dry_run=config.dry_run,
        multi_stage=config.multi_stage,
        extra_tags=config.extra_tags,
    )
    dev_changes, dev_missing = update_dev_stack(dev_config)

    # Then update production stacks in a single PR without auto-merge
    prod_config = UpdateConfig(
        repo=config.repo,
        github_repo=config.github_repo,
        helm_chart=config.helm_chart,
        image_tag=config.image_tag,
        automerge=False,  # Never auto-merge prod in multi-stage
        dry_run=config.dry_run,
        multi_stage=config.multi_stage,
        extra_tags=config.extra_tags,
    )
    prod_changes, prod_missing = update_all_stacks_single_pr(
        prod_config, exclude_stacks=DEV_STACKS
    )

    # Combine results
    changes = dev_changes + prod_changes
    missing_tags = list(set(dev_missing + prod_missing))
    return changes, missing_tags
