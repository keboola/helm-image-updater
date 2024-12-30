"""
Git Operations Module for Helm Image Updater

This module handles Git-related operations such as repository setup and client initialization.
It provides functions to interact with both local Git repositories and GitHub's API.

Functions:
    setup_git_client: Sets up Git and GitHub clients with proper authentication

Raises:
    GitOperationError: When Git operations fail
"""

from git import Repo
from github import Github
from github.Repository import Repository
from .config import GITHUB_REPO
from .exceptions import GitOperationError


def setup_git_client(token: str) -> tuple[Repo, Repository]:
    """Set up Git and GitHub clients."""
    try:
        repo = Repo(".")
        github_client = Github(token)
        github_repo = github_client.get_repo(GITHUB_REPO)
        return repo, github_repo
    except Exception as e:
        raise GitOperationError(f"Failed to setup git clients: {e}") from e
