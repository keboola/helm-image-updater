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
        print(f"Automerge: {config.automerge}")
        print(f"Dry run: {config.dry_run}")
        print(f"Multi-stage deployment: {config.multi_stage}")
        
        # Handle target path change
        if config.target_path != ".":
            print(f"Changed to directory: {config.target_path}")
            os.chdir(config.target_path)
        
        # Step 3: Setup I/O layer
        repo = Repo(".")
        github_client = Github(config.github_token)
        github_repo = github_client.get_repo(GITHUB_REPO)
        io_layer = IOLayer(repo, github_repo, config.dry_run)
        
        # Step 4: Prepare plan (reads files, calculates changes)
        plan = prepare_plan(config, io_layer)
        
        # Step 5: Execute plan (writes files, creates PRs)
        if plan.has_changes():
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
        else:
            if config.dry_run:
                print_dry_run_summary([], [])
        
        print("Image tag update process completed")        
    except Exception as e:
        print(f"Unexpected error: {e}")
        sys.exit(1)

def print_dry_run_summary(changes, missing_tags):
    """Print a summary of changes that would be made in dry run mode."""
    print("\nDry run summary:")
    print("Changes that would be made:")
    for change in changes:
        print(f"- Stack: {change['stack']}")
        print(f"  Chart: {change['chart']}")
        print(f"  Tag: {change['tag']}")
        print(f"  Auto-merge: {change['automerge']}")

    if missing_tags:
        print("\nMissing tag.yaml files:")
        for missing in missing_tags:
            print(f"- {missing}")

if __name__ == "__main__":
    main()
