"""
Configuration Module for Helm Image Updater

This module contains configuration settings and data structures used throughout the application.
It defines constants and configuration classes that control
the behavior of the image updating process.

Constants:
    DEV_STACKS: List of stack names considered as development stacks
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
DEV_STACKS = ["dev-keboola-gcp-us-central1"]
EXCLUDED_STACKS = ["dev-keboola-gcp-us-east1-e2e"]
CANARY_STACKS = {
    "canary-orion": {
        "stack": "dev-keboola-canary-orion",
        "base_branch": "canary-orion",
    },
    "canary-ursa": {"stack": "dev-keboola-canary-ursa", "base_branch": "canary-ursa"},
}
IGNORED_FOLDERS = {".venv", "aws", ".git", ".github"}
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
