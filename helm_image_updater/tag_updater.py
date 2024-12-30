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
from .config import UpdateConfig, DEV_STACKS
from .pr_manager import create_pr
from .utils import random_suffix


def update_tag_yaml(
    stack_folder, helm_chart, image_tag, extra_tags=None, dry_run=False
):
    """Update the tag.yaml file with the new image tag and optional additional tags.

    Args:
        stack_folder (str): The folder of the stack to update.
        helm_chart (str): The name of the Helm chart.
        image_tag (str): The new image tag to set.
        extra_tags (list): Optional list of dicts with 'path' and 'value' keys for additional tags.
                          Example: [{'path': 'agent.image.tag', 'value': 'v1.2.3'}]
        dry_run (bool): Whether to perform a dry run without making actual changes. Defaults to False.

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
    return update_all_stacks_single_pr(config)


def update_stack(config: UpdateConfig, stack: str):
    """Update a single stack with the new image tag."""
    result = update_tag_yaml(
        stack,
        config.helm_chart,
        config.image_tag,
        extra_tags=config.extra_tags,
        dry_run=config.dry_run,
    )
    if result:
        return {
            "stack": stack,
            "chart": config.helm_chart,
            "tag": config.image_tag,
            "automerge": False,
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


def update_all_stacks_single_pr(config: UpdateConfig):
    """Update all stacks in a single PR."""
    changes = []
    missing_tags = []
    branch_name = f"{config.helm_chart}-all-stacks-{config.image_tag}-{random_suffix()}"

    if not config.dry_run:
        config.repo.git.checkout("-b", branch_name)

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
            ):
                if not config.dry_run:
                    config.repo.git.add(tag_file_path)
                changes_made = True
                changes.append(
                    {
                        "stack": stack,
                        "chart": config.helm_chart,
                        "tag": config.image_tag,
                        "automerge": True,
                    }
                )

    if changes_made:
        image_tag_str = (
            f"@{config.image_tag}"
            if config.image_tag
            else ""
            + "".join(
                f" {tag['path']}@{tag['value']}" for tag in (config.extra_tags or [])
            )
        )
        if not config.dry_run:
            config.repo.git.commit(
                "-m",
                f"Update {config.helm_chart} to {image_tag_str} in all stacks",
            )
        pr_title_prefix = (
            "[multi-stage] [production sync]"
            if config.multi_stage
            else "[production sync]"
        )
        create_pr(
            config,
            branch_name,
            f"{pr_title_prefix} {config.helm_chart}{image_tag_str} in all stacks",
        )
    else:
        print("No changes needed for production stacks.")

    return changes, missing_tags


def handle_dev_tag(config: UpdateConfig):
    """Handle dev tag updates."""
    return update_dev_stack(config)


def handle_production_tag(config: UpdateConfig):
    """Handle production tag updates."""
    return update_production_stacks(config)
