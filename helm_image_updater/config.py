"""
Configuration Module for Helm Image Updater

This module contains configuration constants used throughout the application
that control the behavior of the image updating process.

Constants:
    DEV_STACK_MAPPING: Dictionary mapping cloud providers to development stack names
    SUPPORTED_CLOUD_PROVIDERS: List of supported cloud provider names
    CANARY_STACKS: Dictionary of canary stack names and their corresponding tag prefixes
    IGNORED_FOLDERS: Set of folder names to ignore during processing
    GITHUB_REPO: Name of the GitHub repository (from environment)
    GITHUB_BRANCH: Name of the GitHub branch (from environment)
"""

import os

# Constants
DEV_STACK_MAPPING = {
    "gcp": "dev-keboola-gcp-us-central1",
    "azure": "kbc-testing-azure-east-us-2",
    "aws": "dev-keboola-aws-eu-west-1"
}
SUPPORTED_CLOUD_PROVIDERS = ["aws", "azure", "gcp"]
EXCLUDED_STACKS = ["dev-keboola-gcp-us-east1-e2e", "dev-keboola-gcp-e2e-tags", "dev-keboola-azure-east-us-2-e2e", "dev-keboola-aws-us-east-1-e2e"]
CANARY_STACKS = {
    "canary-orion": {
        "stack": "dev-keboola-canary-orion",
        "base_branch": "canary-orion",
    },
}
IGNORED_FOLDERS = {".venv", "aws", ".git", ".github", "utils", "docs", "apps"}
GITHUB_REPO = os.getenv("GITHUB_REPOSITORY", "keboola/kbc-stacks")
GITHUB_BRANCH = "main"
