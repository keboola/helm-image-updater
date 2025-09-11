#!/usr/bin/env python3

"""
Image Tag Update Script for Helm Charts

Simplified CLI using the Functional Core, Imperative Shell pattern.
All business logic is in pure functions, all I/O is in the I/O layer.
"""

import os
import sys
from git import Repo
from github import Github

from .config import GITHUB_REPO
from .environment import EnvironmentConfig
from .io_layer import IOLayer
from .plan_builder import prepare_plan
from .plan_executor import execute_plan


def main():
    """Main entry point - Clean planning/execution pipeline."""
    try:
        # Step 1: Parse environment
        config = EnvironmentConfig.from_env(os.environ)
        
        # Step 2: Validate configuration
        errors = config.validate()
        if errors:
            for error in errors:
                print(f"Error: {error}")
            sys.exit(1)
        
        # Print configuration
        print(f"Processing Helm chart: {config.helm_chart}")
        if config.image_tag:
            print(f"New image tag: {config.image_tag}")
        if config.extra_tags:
            print("Extra tags to update:")
            for tag in config.extra_tags:
                print(f"  - {tag['path']}: {tag['value']}")
        print(f"Automerge: {config.automerge}")
        print(f"Dry run: {config.dry_run}")
        print(f"Multi-stage deployment: {config.multi_stage}")
        
        # Handle target path change
        if config.target_path != ".":
            print(f"Changing to target directory: {config.target_path}")
            os.chdir(config.target_path)
        
        # Step 3: Setup I/O layer
        repo = Repo(".")
        github_client = Github(config.github_token)
        github_repo = github_client.get_repo(GITHUB_REPO)
        io_layer = IOLayer(repo, github_repo, config.dry_run)
        
        # Step 4: Prepare plan (reads files, calculates changes)
        plan = prepare_plan(config, io_layer)
        
        # Step 5: Execute plan (writes files, creates PRs)
        result = execute_plan(plan, io_layer)
        
        # Handle execution results
        if not result.success:
            for error in result.errors:
                print(f"Error: {error}")
            sys.exit(1)
        
        # Show results
        if result.pr_urls:
            print(f"Created {len(result.pr_urls)} PR(s):")
            for url in result.pr_urls:
                print(f"  - {url}")
        elif config.dry_run and not plan.has_changes():
            print("\nDry run summary:")
            print("No changes needed - all tag files are already up to date.")
        
        print("Image tag update process completed")        
    except Exception as e:
        print(f"Unexpected error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
