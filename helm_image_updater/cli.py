#!/usr/bin/env python3

"""
Image Tag Update Script for Helm Charts

This script automates updating image tags for Helm charts across
different stacks in a GitHub repository.
It creates pull requests for updates and optionally auto-merges them.

The script handles different scenarios based on the image tag prefix:
- 'dev-': Updates only dev stacks (specified in DEV_STACKS)
- 'production-': Updates all stacks

Environment Variables:
    HELM_CHART: Name of the Helm chart to update
    IMAGE_TAG: New image tag to set
    GH_TOKEN: GitHub access token for authentication
    AUTOMERGE: Whether to automatically merge created PRs (default: "true")
    DRY_RUN: Whether to perform a dry run without making actual changes (default: "false")
    TARGET_PATH: Path to the directory containing the stacks (default: ".")
    OVERRIDE_STACK: Stack ID to explicitly target for the update, bypassing automatic stack selection. (default: "")

Usage:
    This script is intended to be run as part of a GitHub Actions workflow.
    It assumes that the repository has been checked out
    and the necessary environment variables have been set.

Dependencies:
    PyYAML, GitPython, PyGithub, dpath
"""

import logging
import os
import re
import sys
from pathlib import Path
from git import Repo
from github import Github
from .config import UpdateConfig, GITHUB_REPO, IGNORED_FOLDERS, CANARY_STACKS
from .exceptions import ImageUpdaterError
from .tag_updater import (
    handle_dev_tag,
    handle_production_tag,
    handle_canary_tag,
    update_stack_by_id,
)
from .utils import print_dry_run_summary

logger = logging.getLogger(__name__)


def main():
    """Main function to handle image tag updates across stacks.

    This function reads environment variables, sets up the Git and GitHub clients,
    and calls the appropriate update functions based on the image tag prefix.
    """
    try:
        helm_chart = os.environ["HELM_CHART"]
        image_tag = os.environ.get("IMAGE_TAG", "").strip()
        github_token = os.environ["GH_TOKEN"]
        automerge = os.environ.get("AUTOMERGE", "true").lower() == "true"
        dry_run = os.environ.get("DRY_RUN", "false").lower() == "true"
        multi_stage = os.environ.get("MULTI_STAGE", "false").lower() == "true"
        target_path = os.environ.get("TARGET_PATH", ".")
        commit_sha = os.environ.get("COMMIT_PIPELINE_SHA", "false").lower() == "true"
        override_stack = os.environ.get("OVERRIDE_STACK", "").strip()

        # Change to target directory if specified
        if target_path != ".":
            print(f"Changing to target directory: {target_path}")
            os.chdir(target_path)

        # Build extra_tags array from environment variables
        extra_tags = []
        for i in range(1, 3):
            if tag_str := os.environ.get(f"EXTRA_TAG{i}", "").strip():
                try:
                    path, value = tag_str.split(":", 1)
                    if not value.strip():
                        print(f"Error: EXTRA_TAG{i} value cannot be empty")
                        sys.exit(1)
                    extra_tags.append({"path": path, "value": value.strip()})
                except ValueError:
                    print(f"Error: EXTRA_TAG{i} must be in format 'path:value'")
                    sys.exit(1)

        # Validate that either image_tag or extra_tags are set
        if not image_tag and not extra_tags:
            print("Error: Either IMAGE_TAG or at least one EXTRA_TAG must be set")
            sys.exit(1)

        # Validate image_tag format if it's set
        if image_tag.strip():
            valid_prefixes = ["dev-", "production-"] + list(CANARY_STACKS.keys())
            if not (
                any(image_tag.startswith(prefix) for prefix in valid_prefixes)
                or re.match(r"^v?\d+\.\d+\.\d+$", image_tag)
            ):
                print(
                    "Invalid image tag format. Must start with 'dev-', 'production-', "
                    f"be a semver (0.1.2 or v0.1.2), or one of {', '.join(CANARY_STACKS.keys())}."
                )
                sys.exit(1)

        if extra_tags:
            for tag in extra_tags:
                if not (
                    tag["value"].startswith("dev-")
                    or tag["value"].startswith("production-")
                    or re.match(r"^v?\d+\.\d+\.\d+$", tag["value"])
                ):
                    print(
                        f"Invalid extra tag format for {tag['path']}: {tag['value']}. Must start with 'dev-' or 'production-', or be a semver (0.1.2 or v0.1.2)."
                    )
                    sys.exit(1)

        print(f"Processing Helm chart: {helm_chart}")
        if image_tag:
            print(f"New image tag: {image_tag}")
        print(f"Automerge: {automerge}")
        print(f"Dry run: {dry_run}")
        print(f"Multi-stage deployment: {multi_stage}")
        if override_stack:
            print(f"Override stack: {override_stack}")
        if extra_tags:
            print("Extra tags to update:")
            for tag in extra_tags:
                print(f"  - {tag['path']}: {tag['value']}")

        repo = Repo(".")
        github_client = Github(github_token)
        github_repo = github_client.get_repo(GITHUB_REPO)

        # Check if this is a canary tag and switch to the appropriate branch first
        is_canary_tag = False
        canary_branch = None

        # Check main image tag for canary prefixes
        if image_tag:
            for prefix, stack_info in CANARY_STACKS.items():
                if image_tag.startswith(prefix):
                    is_canary_tag = True
                    canary_branch = stack_info["base_branch"]
                    print(
                        f"Detected canary tag with prefix '{prefix}', switching to branch '{canary_branch}'"
                    )
                    break

        # Also check extra tags for canary prefixes
        if not is_canary_tag and extra_tags:
            for tag in extra_tags:
                for prefix, stack_info in CANARY_STACKS.items():
                    if tag["value"].startswith(prefix):
                        is_canary_tag = True
                        canary_branch = stack_info["base_branch"]
                        print(
                            f"Detected canary tag in extra_tags with prefix '{prefix}', switching to branch '{canary_branch}'"
                        )
                        break
                if is_canary_tag:
                    break

        # Switch to canary branch if detected
        if is_canary_tag and canary_branch:
            try:
                repo.git.checkout(canary_branch)
                repo.git.pull("origin", canary_branch)
                print(f"Successfully switched to branch '{canary_branch}'")
            except Exception as e:
                print(f"Warning: Could not switch to branch '{canary_branch}': {e}")
                # Continue with current branch

        # Add debug logging for directory checking
        print(f"\nChecking for tag.yaml files in current directory: {os.getcwd()}")
        print(f"Current branch: {repo.active_branch.name}")
        print(
            "Available directories:",
            [
                d
                for d in os.listdir(".")
                if os.path.isdir(d) and d not in IGNORED_FOLDERS
            ],
        )

        tag_yaml_exists = any(
            Path(f"{stack}/{helm_chart}/tag.yaml").exists()
            for stack in os.listdir(".")
            if os.path.isdir(f"{stack}") and stack not in IGNORED_FOLDERS
        )

        if not tag_yaml_exists:
            print("\nDebug info:")
            print(f"Helm chart: {helm_chart}")
            print("Searching for tag.yaml in these locations:")
            for stack in os.listdir("."):
                if os.path.isdir(f"{stack}") and stack not in IGNORED_FOLDERS:
                    path = Path(f"{stack}/{helm_chart}/tag.yaml")
                    print(f"- {path} (exists: {path.exists()})")
            print(
                f"\nError: tag.yaml for chart {helm_chart} does not exist in any stack"
            )
            sys.exit(1)

        config = UpdateConfig(
            repo=repo,
            github_repo=github_repo,
            helm_chart=helm_chart,
            image_tag=image_tag,
            automerge=automerge,
            dry_run=dry_run,
            multi_stage=multi_stage,
            extra_tags=extra_tags if extra_tags else None,
            commit_sha=commit_sha,
            user_requested_automerge=automerge,
        )

        # Group extra tags by value and join paths with the same value
        extra_tags_contains_dev = config.extra_tags and any(
            tag["value"].startswith("dev-") for tag in config.extra_tags
        )
        extra_tags_contains_production = config.extra_tags and any(
            tag["value"].startswith("production-") for tag in config.extra_tags
        )
        extra_tags_contains_canary = config.extra_tags and any(
            any(image_tag.startswith(prefix) for prefix in CANARY_STACKS.keys())
            for tag in config.extra_tags
        )
        extra_tags_contains_semver = config.extra_tags and any(
            re.match(r"^v?\d+\.\d+\.\d+$", tag["value"]) for tag in config.extra_tags
        )

        if override_stack:
            changes, missing_tags = update_stack_by_id(config, override_stack)
        elif image_tag.startswith("dev-") or extra_tags_contains_dev:
            changes, missing_tags = handle_dev_tag(config)
        elif (
            # Production tag formats
            image_tag.startswith("production-")
            or
            # Semver formats in main tag
            re.match(r"^v?\d+\.\d+\.\d+$", image_tag)
            or
            # Extra tags with production or semver
            extra_tags_contains_production
            or extra_tags_contains_semver
        ):
            changes, missing_tags = handle_production_tag(config)
        elif (
            any(image_tag.startswith(prefix) for prefix in CANARY_STACKS.keys())
            or extra_tags_contains_canary
        ):
            changes, missing_tags = handle_canary_tag(config)
        else:
            print(
                "Invalid image tag format. Must start with 'dev-' or 'production-', or be a semver (0.1.2 or v0.1.2)."
            )
            sys.exit(1)

        if dry_run:
            print_dry_run_summary(changes, missing_tags)

        print("Image tag update process completed")

    except ImageUpdaterError as e:
        logger.error("Error updating image tags: %s", e)
        sys.exit(1)
    except (ValueError, OSError, IOError):
        logger.exception("System or IO error occurred")
        sys.exit(1)


if __name__ == "__main__":
    main()
