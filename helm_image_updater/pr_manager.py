"""
Pull Request Manager Module for Helm Image Updater

This module handles the creation and management of GitHub pull requests.
It provides functionality to create PRs with detailed descriptions,
including metadata about the changes and optional auto-merge capabilities.

Functions:
    create_pr_body: Generates a formatted PR description with change details
    create_pr: Creates a GitHub PR and optionally auto-merges it

Dependencies:
    github.Repository
    github.GithubException
"""

import os
import json
from pathlib import Path
from time import sleep
from github.GithubException import GithubException
from .config import UpdateConfig, GITHUB_BRANCH
from .utils import get_trigger_metadata


def create_pr_body(config: UpdateConfig) -> str:
    """Generate the PR body with trigger information.

    Args:
        config (UpdateConfig): The configuration object.

    Returns:
        str: Formatted PR body
    """
    metadata = get_trigger_metadata()

    # Common links section that appears in both cases
    monitoring_links = (
        "### 📊 Monitoring\n"
        f"- [🎯 Argo CD](https://argo.keboola.tech/applications?labels=app%253D{config.helm_chart})\n"
        f"- [🐕 Datadog](https://app.datadoghq.eu/ci/deployments?search=%40deployment.service%3A{config.helm_chart})"
    )

    # Change summary that appears in both cases
    change_summary = (
        "### 📝 Changes\n"
        f"- **Chart:** `{config.helm_chart}`\n"
        f"- **New Tag:** `{config.image_tag}`"
    )

    if metadata:
        # Pipeline trigger case
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
    else:
        # Manual trigger case - group variables into context dict
        try:
            github_context_path = Path(os.getcwd()) / "github_context.json"
            with open(github_context_path, encoding="utf-8") as f:
                ctx = json.load(f)

            urls = {
                "repo": f"{ctx.get('server_url', 'https://github.com')}/{ctx.get('repository', '')}",
                "workflow": f"{ctx.get('server_url', 'https://github.com')}/{ctx.get('repository', '')}/actions/runs/{ctx.get('run_id', '')}",
            }

            trigger_info = (
                "### 🔄 Manual Workflow Trigger\n"
                "#### Trigger Details\n"
                f"- **Actor:** {ctx.get('actor', 'Unknown')}\n"
                f"- **Repository:** [{ctx.get('repository', '')}]({urls['repo']})\n"
                f"- **Branch:** `{ctx.get('ref_name', '')}`\n\n"
                "#### Workflow Information\n"
                f"- **Workflow:** [{ctx.get('workflow_ref', '')}]({urls['workflow']})\n"
                f"- **Run:** [#{ctx.get('run_number', '')}]({urls['workflow']})\n\n"
                "#### Configuration\n"
                f"- **Auto-merge:** `{ctx.get('event', {}).get('inputs', {}).get('automerge', str(config.automerge))}`\n"
                f"- **Dry Run:** `{ctx.get('event', {}).get('inputs', {}).get('dry-run', str(config.dry_run))}`"
            )
        except (IOError, json.JSONDecodeError) as e:
            print(f"Warning: Failed to read GitHub context: {e}")
            trigger_info = (
                "### 🔄 Manual Trigger\n- **Source:** GitHub Actions workflow"
            )

    # Combine all sections with clear separation
    return (
        f"## 🤖 Automated Image Tag Update\n\n"
        f"{trigger_info}\n\n"
        f"{change_summary}\n\n"
        f"{monitoring_links}"
    )


def create_pr(
    config: UpdateConfig,
    branch_name: str,
    pr_title: str,
    base: str = GITHUB_BRANCH,
):
    """Create a pull request for the changes."""
    pr_body = create_pr_body(config)

    # Determine if this PR should be auto-merged
    should_automerge = config.automerge
    if config.multi_stage:
        # In multi-stage mode:
        # - Auto-merge only dev PRs (those with [test sync])
        # - Never auto-merge production PRs (those with [production sync])
        should_automerge = "[test sync]" in pr_title
    elif "[canary sync]" in pr_title:
        # Always auto-merge canary PRs
        should_automerge = True

    if config.dry_run:
        automerge_status = (
            "and automatically merge it" if should_automerge else "without auto-merging"
        )
        print(f"Would create PR: '{pr_title}' {automerge_status}")
        print("PR body would be:")
        print(pr_body)
    else:
        config.repo.git.push("origin", branch_name)
        pr = config.github_repo.create_pull(
            title=pr_title,
            body=pr_body,
            head=branch_name,
            base=base,
        )
        if should_automerge:
            max_retries = 5
            retry_delay = 5  # seconds
            for attempt in range(max_retries):
                try:
                    # Check if PR is mergeable
                    pr.update()  # Refresh PR data
                    if pr.mergeable is None:
                        print(
                            f"PR mergeability not yet determined, waiting {retry_delay} seconds... (attempt {attempt + 1}/{max_retries})"
                        )
                        sleep(retry_delay)
                        continue
                    elif not pr.mergeable:
                        print(f"PR is not mergeable due to conflicts: {pr.html_url}")
                        break

                    pr.merge()
                    print(f"PR created and automatically merged: {pr.html_url}")
                    break
                except GithubException as e:
                    error_message = str(e.data.get("message", "")).lower()
                    if e.status == 405 and (
                        "not mergeable" in error_message
                        or "merge already in progress" in error_message
                    ):
                        if attempt < max_retries - 1:
                            print(
                                f"PR merge failed ({error_message}), waiting {retry_delay} seconds... (attempt {attempt + 1}/{max_retries})"
                            )
                            sleep(retry_delay)
                        else:
                            print(
                                f"Failed to merge PR after {max_retries} attempts: {pr.html_url}"
                            )
                            if "merge already in progress" in error_message:
                                print(
                                    "Note: PR might still be merged by GitHub's background process"
                                )
                            raise
                    else:
                        raise  # Re-raise if it's a different error
        else:
            print(f"PR created (without auto-merging): {pr.html_url}")
