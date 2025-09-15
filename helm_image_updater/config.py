"""
Configuration Module for Helm Image Updater

This module contains configuration settings and data structures used throughout the application.
It defines constants and configuration classes that control
the behavior of the image updating process.

Constants:
    DEV_STACK_MAPPING: Dictionary mapping cloud providers to development stack names
    SUPPORTED_CLOUD_PROVIDERS: List of supported cloud provider names
    CANARY_STACKS: Dictionary of canary stack names and their corresponding tag prefixes
    IGNORED_FOLDERS: Set of folder names to ignore during processing
    GITHUB_REPO: Name of the GitHub repository (from environment)
    GITHUB_BRANCH: Name of the GitHub branch (from environment)

Classes:
    UpdateConfig: Configuration class holding settings for update operations
"""

from dataclasses import dataclass
from typing import List, Optional
import os
from git import Repo
from github.Repository import Repository

# Constants
DEV_STACK_MAPPING = {
    "gcp": "dev-keboola-gcp-us-central1",
    "azure": "kbc-testing-azure-east-us-2",
    "aws": "dev-keboola-aws-eu-west-1"
}
SUPPORTED_CLOUD_PROVIDERS = ["aws", "azure", "gcp"]
EXCLUDED_STACKS = ["dev-keboola-gcp-us-east1-e2e", "dev-keboola-azure-east-us-2-e2e", "dev-keboola-aws-us-east-1-e2e"]
CANARY_STACKS = {
    "canary-orion": {
        "stack": "dev-keboola-canary-orion",
        "base_branch": "canary-orion",
    },
}
IGNORED_FOLDERS = {".venv", "aws", ".git", ".github", "utils", "docs", "apps"}
GITHUB_REPO = os.getenv("GITHUB_REPOSITORY", "keboola/kbc-stacks")
GITHUB_BRANCH = "main"


@dataclass
class UpdateConfig:
    """Configuration class for update operations."""

    repo: Repo
    github_repo: Repository
    helm_chart: str
    image_tag: str
    automerge: bool
    dry_run: bool = False
    multi_stage: bool = False
    extra_tags: Optional[List[dict]] = None
    commit_sha: bool = False
    user_requested_automerge: Optional[bool] = None
