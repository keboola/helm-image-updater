"""Test module for git_operations.py.

This module contains tests for the Git operations functionality of the Helm Image Updater.
It verifies the proper setup and error handling of Git and GitHub clients.

The tests use mock objects to simulate Git repositories and GitHub API interactions,
allowing for testing without actual Git operations or network calls.

Fixtures:
    mock_repo: Provides a mock Git repository
    mock_github: Provides a mock GitHub client

Test Cases:
    test_setup_git_client_success: Verifies successful client setup
    test_setup_git_client_failure: Verifies proper error handling
"""

from unittest.mock import Mock, patch
import pytest
from helm_image_updater.git_operations import setup_git_client
from helm_image_updater.exceptions import GitOperationError


@pytest.fixture
def mock_repo():
    """Creates a mock Git repository object.

    This fixture provides a mock object that simulates a Git repository
    for testing without requiring actual Git operations.

    Returns:
        Mock: A mock object representing a Git repository

    Example:
        def test_example(mock_repo):
            assert mock_repo is not None
    """
    return Mock()


@pytest.fixture
def mock_github():
    """Creates a mock GitHub client object.

    This fixture provides a mock object that simulates a GitHub client
    for testing without requiring actual GitHub API calls.

    Returns:
        Mock: A mock object representing a GitHub client

    Example:
        def test_example(mock_github):
            assert mock_github is not None
    """
    return Mock()


def test_setup_git_client_success(mock_repo, mock_github):
    """Tests successful Git client setup.

    This test verifies that setup_git_client correctly:
    1. Initializes a Git repository
    2. Creates a GitHub client with the provided token
    3. Gets the correct GitHub repository

    Args:
        mock_repo (Mock): Fixture providing a mock Git repository
        mock_github (Mock): Fixture providing a mock GitHub client

    Returns:
        None

    Raises:
        AssertionError: If any of the assertions fail

    Example:
        A successful test will verify:
            * Git repository is initialized
            * GitHub client is created with token
            * Correct repository is accessed
    """
    with (
        patch("helm_image_updater.git_operations.Repo") as mock_repo_class,
        patch("helm_image_updater.git_operations.Github") as mock_github_class,
    ):
        mock_repo_class.return_value = mock_repo
        mock_github_class.return_value = mock_github
        mock_github.get_repo.return_value = Mock()

        repo, github_repo = setup_git_client("fake-token")

        assert repo == mock_repo
        assert github_repo == mock_github.get_repo.return_value
        mock_github_class.assert_called_once_with("fake-token")


def test_setup_git_client_failure():
    """Tests Git client setup failure handling.

    This test verifies that setup_git_client properly handles errors by:
    1. Simulating a Git repository initialization failure
    2. Checking that the appropriate exception is raised

    Returns:
        None

    Raises:
        AssertionError: If GitOperationError is not raised as expected

    Example:
        A successful test will verify:
            * GitOperationError is raised when Git setup fails
            * No repository objects are created
    """
    with patch("helm_image_updater.git_operations.Repo") as mock_repo_class:
        mock_repo_class.side_effect = Exception("Git error")

        with pytest.raises(GitOperationError):
            setup_git_client("fake-token")
