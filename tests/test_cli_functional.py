#!/usr/bin/env python3

"""
Functional tests for the CLI module.

These tests verify the full functionality chain of the CLI without making actual external API calls.
They test that the CLI correctly:
1. Handles environment variables and configuration
2. Processes different types of image tags (dev, production, canary)
3. Updates tag.yaml files correctly based on tag type and stack configuration
4. Creates simulated PRs with the correct properties

Note: These tests mock external dependencies (Git, GitHub API) while testing the actual
file operations with temporary directories.
"""

import os
import pytest
import yaml
from unittest.mock import Mock, patch, MagicMock

# Import the modules we'll need
from helm_image_updater import cli

# -----------------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------------


@pytest.fixture
def mock_github():
    """Provides a mock GitHub client and repo for PR verification."""
    github_mock = MagicMock()
    repo_mock = MagicMock()
    github_mock.get_repo.return_value = repo_mock

    # Mock pull request creation
    pull_mock = MagicMock()
    repo_mock.create_pull.return_value = pull_mock

    # Return values for the PR creation
    pull_mock.html_url = "https://github.com/mock-org/mock-repo/pull/123"
    pull_mock.number = 123

    return github_mock, repo_mock, pull_mock


@pytest.fixture
def mock_repo():
    """Provides a mock Git repository."""
    repo = Mock()
    repo.git = Mock()
    return repo


@pytest.fixture
def mock_github_repo():
    """Provides a mock GitHub repository."""
    return Mock()


@pytest.fixture
def mock_git_operations():
    """Mock all Git/GitHub operations in IOLayer, allowing file writing to happen."""
    
    with (
        patch("helm_image_updater.io_layer.IOLayer.checkout_branch", return_value=True) as mock_checkout,
        patch("helm_image_updater.io_layer.IOLayer.add_files", return_value=True) as mock_add,
        patch("helm_image_updater.io_layer.IOLayer.commit", return_value=True) as mock_commit,
        patch("helm_image_updater.io_layer.IOLayer.push_branch", return_value=True) as mock_push,
        patch("helm_image_updater.io_layer.IOLayer.create_pull_request") as mock_pr,
    ):
        # Default PR creation behavior
        mock_pr.return_value = "https://github.com/mock/pull/123"
        
        yield {
            'checkout_branch': mock_checkout,
            'add_files': mock_add,
            'commit': mock_commit,
            'push_branch': mock_push,
            'create_pull_request': mock_pr,
        }


@pytest.fixture
def cli_test_env(mock_repo, mock_github_repo, tmp_path):
    """Setup test environment for CLI tests."""
    # Create test stack structure
    base_dir = tmp_path
    setup_test_stacks(base_dir)

    # Store original environment and directory
    orig_env = os.environ.copy()
    orig_dir = os.getcwd()

    # Change to test directory
    os.chdir(base_dir)

    # Setup patches for external dependencies
    with (
        patch("helm_image_updater.config.GITHUB_REPO", "mock-org/mock-repo"),
        patch("helm_image_updater.cli.Repo", return_value=mock_repo),
        patch("helm_image_updater.cli.Github", return_value=Mock(get_repo=lambda x: mock_github_repo)),
    ):
        # Clear environment and set basic variables
        os.environ.clear()
        os.environ["GH_TOKEN"] = "fake-token"

        yield base_dir, mock_repo, mock_github_repo

    # Restore original environment and directory
    os.chdir(orig_dir)
    os.environ.clear()
    os.environ.update(orig_env)


# -----------------------------------------------------------------------------
# Helper Functions
# -----------------------------------------------------------------------------


def setup_test_stacks(base_path):
    """Create test stack structure with tag.yaml and shared-values.yaml files."""
    # Create dev stacks (3 clouds)
    create_stack_with_shared_values(base_path / "dev-keboola-gcp-us-central1", "gcp")
    create_stack_with_shared_values(base_path / "kbc-testing-azure-east-us-2", "azure")
    create_stack_with_shared_values(base_path / "dev-keboola-aws-eu-west-1", "aws")

    # Create production stacks (3 clouds) 
    create_stack_with_shared_values(base_path / "com-keboola-gcp-prod", "gcp")
    create_stack_with_shared_values(base_path / "com-keboola-azure-prod", "azure")
    create_stack_with_shared_values(base_path / "com-keboola-aws-prod", "aws")

    # Create canary stack
    create_stack_with_shared_values(base_path / "dev-keboola-canary-orion", "gcp")

    # Create e2e dev stack (excluded)
    create_stack_with_shared_values(base_path / "dev-keboola-gcp-us-east1-e2e", "gcp")


def create_stack_with_shared_values(stack_path, cloud_provider):
    """Helper to create stack with both tag.yaml and shared-values.yaml."""
    stack_path.mkdir()
    (stack_path / "test-chart").mkdir()
    create_tag_yaml(stack_path / "test-chart" / "tag.yaml", "old-tag")
    
    # Create shared-values.yaml
    shared_values = {"cloudProvider": cloud_provider}
    with open(stack_path / "shared-values.yaml", "w") as f:
        yaml.dump(shared_values, f)


def create_tag_yaml(path, tag):
    """Helper to create tag.yaml files."""
    data = {"image": {"tag": tag}}
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f)


def read_tag_yaml(path):
    """Helper to read tag.yaml files."""
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# -----------------------------------------------------------------------------
# Environment Variable Handling Tests
# -----------------------------------------------------------------------------


def test_cli_environment_variables(cli_test_env, capsys):
    """Test CLI environment variable handling."""
    base_dir, mock_repo, mock_github_repo = cli_test_env

    # Set environment variables
    os.environ["HELM_CHART"] = "test-chart"
    os.environ["IMAGE_TAG"] = "dev-1.2.3"
    os.environ["AUTOMERGE"] = "false"
    os.environ["DRY_RUN"] = "true"

    # Run CLI
    cli.main()

    # Check output
    captured = capsys.readouterr()
    assert "Processing Helm chart: test-chart" in captured.out
    assert "New image tag: dev-1.2.3" in captured.out
    assert "Automerge: False" in captured.out
    assert "Dry run: True" in captured.out


# -----------------------------------------------------------------------------
# Tag Workflow Tests
# -----------------------------------------------------------------------------


def test_dev_tag_update(cli_test_env, mock_git_operations, capsys):
    """Test updating dev stacks with a dev tag.

    This test verifies that:
    1. Only dev stacks are updated with dev tags
    2. The tag.yaml file content is correctly modified
    3. Console output correctly reports the updates
    """
    base_dir, mock_repo, mock_github_repo = cli_test_env

    # Set environment variables for dev tag update
    os.environ["HELM_CHART"] = "test-chart"
    os.environ["IMAGE_TAG"] = "dev-1.2.3"
    os.environ["AUTOMERGE"] = "true"

    # Track PR creation calls
    created_prs = []

    def track_pr_creation(*args, **kwargs):
        """Track GitHub PR creation details."""
        # Extract arguments (self, title, body, branch_name, base_branch="main", auto_merge=False)
        if len(args) >= 4:
            title, body, branch_name = args[1], args[2], args[3]
            base_branch = args[4] if len(args) > 4 else kwargs.get("base_branch", "main")
        else:
            title = kwargs.get("title", "Unknown")
            body = kwargs.get("body", "")
            branch_name = kwargs.get("branch_name", "unknown-branch")
            base_branch = kwargs.get("base_branch", "main")
        
        created_prs.append({"branch": branch_name, "title": title, "base": base_branch})
        print(f"Created PR: {title} (branch: {branch_name}, base: {base_branch})")
        return "https://github.com/mock-org/mock-repo/pull/123"

    # Customize the PR creation mock to track calls
    mock_git_operations['create_pull_request'].side_effect = track_pr_creation

    # Run CLI - files will be written, Git/GitHub operations mocked
    cli.main()

    # Check console output
    captured = capsys.readouterr()
    assert "Processing Helm chart: test-chart" in captured.out
    assert "New image tag: dev-1.2.3" in captured.out
    assert "Updating dev stacks (dev- tag)" in captured.out

     # Verify tag.yaml was updated in dev stack
    dev_tag_yaml = read_tag_yaml(
        base_dir / "dev-keboola-gcp-us-central1" / "test-chart" / "tag.yaml"
    )
    assert dev_tag_yaml["image"]["tag"] == "dev-1.2.3"
    assert "Updated dev-keboola-gcp-us-central1/test-chart/tag.yaml" in captured.out

    # Verify tag.yaml was NOT updated in prod stack
    prod_tag_yaml = read_tag_yaml(
        base_dir / "com-keboola-gcp-prod" / "test-chart" / "tag.yaml"
    )
    assert prod_tag_yaml["image"]["tag"] == "old-tag"

    # Verify Git operations were performed
    assert mock_git_operations['checkout_branch'].called, "git checkout should be called"
    assert mock_git_operations['add_files'].called, "git add should be called"
    assert mock_git_operations['commit'].called, "git commit should be called"
    assert mock_git_operations['create_pull_request'].called, "create PR should be called"
    
    # Verify PR was created
    assert len(created_prs) == 1
    assert "test-chart" in created_prs[0]["title"]


def test_production_tag_update(cli_test_env, mock_git_operations, capsys):
    """Test updating all stacks with a production tag.

    This test verifies that:
    1. All stacks are updated with production tags
    2. The tag.yaml files are correctly modified
    3. Console output correctly reports the updates
    4. Git operations are performed correctly
    """
    base_dir, mock_repo, mock_github_repo = cli_test_env

    # Set environment variables for production tag update
    os.environ["HELM_CHART"] = "test-chart"
    os.environ["IMAGE_TAG"] = "production-1.2.3"
    os.environ["AUTOMERGE"] = "true"

    # Track PR creation calls
    created_prs = []

    def track_pr_creation(*args, **kwargs):
        """Track GitHub PR creation details."""
        # Extract arguments (self, title, body, branch_name, base_branch="main", auto_merge=False)
        if len(args) >= 4:
            title, body, branch_name = args[1], args[2], args[3]
            base_branch = args[4] if len(args) > 4 else kwargs.get("base_branch", "main")
        else:
            title = kwargs.get("title", "Unknown")
            body = kwargs.get("body", "")
            branch_name = kwargs.get("branch_name", "unknown-branch")
            base_branch = kwargs.get("base_branch", "main")
        
        created_prs.append({"branch": branch_name, "title": title, "base": base_branch})
        print(f"Created PR: {title} (branch: {branch_name}, base: {base_branch})")
        return "https://github.com/mock-org/mock-repo/pull/123"

    # Customize the PR creation mock to track calls
    mock_git_operations['create_pull_request'].side_effect = track_pr_creation
    
    # Run CLI - files will be written, Git/GitHub operations mocked
    cli.main()

    # Check console output
    captured = capsys.readouterr()
    assert "Processing Helm chart: test-chart" in captured.out
    assert "Updating all stacks (production- tag)" in captured.out
    assert "New image tag:" in captured.out

    # Verify tag.yaml was updated in both dev and prod stacks
    dev_tag_yaml = read_tag_yaml(
        base_dir / "dev-keboola-gcp-us-central1" / "test-chart" / "tag.yaml"
    )
    assert dev_tag_yaml["image"]["tag"] == "production-1.2.3"

    prod_tag_yaml = read_tag_yaml(
        base_dir / "com-keboola-gcp-prod" / "test-chart" / "tag.yaml"
    )
    assert prod_tag_yaml["image"]["tag"] == "production-1.2.3"

    # Verify Git operations were performed
    assert mock_git_operations['checkout_branch'].called, "git checkout should be called"
    assert mock_git_operations['add_files'].called, "git add should be called"
    assert mock_git_operations['commit'].called, "git commit should be called"
    assert mock_git_operations['create_pull_request'].called, "create PR should be called"

    # Verify PR was created
    assert len(created_prs) == 1
    assert "test-chart" in created_prs[0]["title"]


def test_canary_tag_update(cli_test_env, capsys):
    """Test updating canary stack with a canary tag.

    This test verifies canary tag handling in two scenarios:
    1. Regular services: Chart exists in multiple environments (test-chart)
    2. Canary-only services: Chart exists only in canary branches (metastore)

    Both scenarios should:
    - Switch to the correct canary branch before file checks
    - Only update the appropriate canary stack
    - Create PR against the correct canary branch
    """
    base_dir, mock_repo, mock_github_repo = cli_test_env

    # Setup mock repo to track git operations and simulate branch switching
    git_calls = []

    def track_git_call(*args, **kwargs):
        git_calls.append(args)
        return Mock()

    mock_repo.git.checkout = Mock(side_effect=track_git_call)
    mock_repo.git.pull = Mock(side_effect=track_git_call)
    mock_repo.active_branch = Mock()
    mock_repo.active_branch.name = "canary-orion"

    # Mock create_pr to track PRs created
    created_prs = []

    def mock_create_branch_commit_and_pr(self, branch_name, files_to_commit, commit_message, pr_title, pr_body, base_branch="main", auto_merge=False):
        """Mock PR creation to track PR details."""
        created_prs.append({"branch": branch_name, "title": pr_title, "base": base_branch})
        print(f"Created PR: {pr_title} (branch: {branch_name}, base: {base_branch})")
        return "https://github.com/mock-org/mock-repo/pull/123"

    # Test Case 1: Regular service that exists in multiple environments
    os.environ["HELM_CHART"] = "test-chart"
    os.environ["IMAGE_TAG"] = "canary-orion-1.2.3"
    os.environ["AUTOMERGE"] = "true"

    with patch("helm_image_updater.io_layer.IOLayer.create_branch_commit_and_pr", mock_create_branch_commit_and_pr):
        cli.main()

    # Check console output for branch switching
    captured = capsys.readouterr()
    assert "Processing Helm chart: test-chart" in captured.out
    assert "Detected canary tag, switching to branch 'canary-orion'" in captured.out
    assert "Successfully switched to branch 'canary-orion'" in captured.out
    assert "Updating canary stack" in captured.out
    assert "New image tag:" in captured.out
    assert "Updated dev-keboola-canary-orion/test-chart/tag.yaml: image.tag from old-tag to canary-orion-1.2.3" in captured.out

    # Verify tag.yaml was updated only in canary stack
    canary_tag_yaml = read_tag_yaml(
        base_dir / "dev-keboola-canary-orion" / "test-chart" / "tag.yaml"
    )
    assert canary_tag_yaml["image"]["tag"] == "canary-orion-1.2.3"

    # Verify other stacks were NOT updated
    dev_tag_yaml = read_tag_yaml(
        base_dir / "dev-keboola-gcp-us-central1" / "test-chart" / "tag.yaml"
    )
    assert dev_tag_yaml["image"]["tag"] == "old-tag"

    prod_tag_yaml = read_tag_yaml(
        base_dir / "com-keboola-gcp-prod" / "test-chart" / "tag.yaml"
    )
    assert prod_tag_yaml["image"]["tag"] == "old-tag"    

    # Verify PR was created against canary branch
    assert len(created_prs) == 1
    assert "test-chart" in created_prs[0]["title"]
    assert created_prs[0]["base"] == "canary-orion"

    # Verify git operations were called for branch switching
    checkout_calls = [call for call in git_calls if "canary-orion" in str(call)]
    assert len(checkout_calls) >= 1, (
        "Should have called git checkout for canary-orion branch"
    )

    # Test Case 2: Canary-only service (like metastore)
    # Reset environment and tracking variables
    created_prs.clear()
    git_calls.clear()
    os.environ.clear()
    os.environ["GH_TOKEN"] = "fake-token"
    os.environ["HELM_CHART"] = "metastore"  # Chart that only exists in canary
    os.environ["IMAGE_TAG"] = "canary-orion-metastore-0.0.5"
    os.environ["AUTOMERGE"] = "true"

    # Create metastore chart only in canary stack (simulating canary-only service)
    metastore_canary_dir = base_dir / "dev-keboola-canary-orion" / "metastore"
    metastore_canary_dir.mkdir()
    create_tag_yaml(metastore_canary_dir / "tag.yaml", "old-canary-tag")

    with patch("helm_image_updater.io_layer.IOLayer.create_branch_commit_and_pr", mock_create_branch_commit_and_pr):
        cli.main()

    # Check console output shows proper branch switching before file checks
    captured = capsys.readouterr()
    assert "Processing Helm chart: metastore" in captured.out
    assert "Detected canary tag, switching to branch 'canary-orion'" in captured.out
    assert "switching to branch 'canary-orion'" in captured.out
    assert "Successfully switched to branch 'canary-orion'" in captured.out

    # Most importantly: verify it didn't exit early due to missing files
    assert (
        "tag.yaml for chart metastore does not exist in any stack" not in captured.out
    )
    assert "New image tag:" in captured.out
    assert "Updating canary stack" in captured.out
    assert "Updated dev-keboola-canary-orion/metastore/tag.yaml: image.tag from old-canary-tag to canary-orion-metastore-0.0.5" in captured.out

    # Verify the canary-only service was updated
    metastore_tag_yaml = read_tag_yaml(metastore_canary_dir / "tag.yaml")
    assert metastore_tag_yaml["image"]["tag"] == "canary-orion-metastore-0.0.5"    

    # Verify PR was created for canary-only service
    assert len(created_prs) == 1
    assert "metastore" in created_prs[0]["title"]
    assert created_prs[0]["base"] == "canary-orion"


# -----------------------------------------------------------------------------
# Target path Tests
# -----------------------------------------------------------------------------


def test_cli_target_path(cli_test_env, tmp_path, capsys):
    """Test CLI target path handling."""
    base_dir, mock_repo, mock_github_repo = cli_test_env

    # Create a subdirectory with test stacks
    target_dir = tmp_path / "target_dir"
    target_dir.mkdir()

    # Setup test stacks in the target directory
    setup_test_stacks(target_dir)

    # Set environment variables with target path
    os.environ["HELM_CHART"] = "test-chart"
    os.environ["IMAGE_TAG"] = "dev-1.2.3"
    os.environ["TARGET_PATH"] = str(target_dir)

    # Mock os.chdir to verify it's called with the correct path
    with patch("os.chdir") as mock_chdir:
        # Run CLI
        cli.main()

        # Verify chdir was called with correct path
        mock_chdir.assert_called_with(str(target_dir))

    # Verify output
    captured = capsys.readouterr()
    assert f"Changing to target directory: {target_dir}" in captured.out


# -----------------------------------------------------------------------------
# Error Handling Tests
# -----------------------------------------------------------------------------


def test_missing_required_env_var(cli_test_env, mock_git_operations, capsys):
    """Test error handling for missing environment variables.

    This test verifies that:
    1. Missing HELM_CHART env var is detected and validation fails
    2. The script exits with code 1 and prints error message
    3. No PRs are created
    """
    base_dir, mock_repo, mock_github_repo = cli_test_env

    # Don't set HELM_CHART
    os.environ["IMAGE_TAG"] = "dev-1.2.3"

    # Run CLI expecting SystemExit due to validation failure
    with pytest.raises(SystemExit) as exc_info:
        cli.main()

    # Verify exit code is 1
    assert exc_info.value.code == 1

    # Verify error message is printed
    captured = capsys.readouterr()
    assert "Error: HELM_CHART is required" in captured.out

    # Verify no PRs were created
    assert mock_git_operations['create_pull_request'].call_count == 0


def test_invalid_tag_format(cli_test_env, mock_git_operations, capsys):
    """Test error handling for invalid tag format.

    This test verifies that:
    1. Invalid tag format is detected during validation
    2. The script exits with code 1
    3. Appropriate error message is displayed
    4. No PRs are created
    """
    base_dir, mock_repo, mock_github_repo = cli_test_env

    # Set environment variables with invalid tag
    os.environ["HELM_CHART"] = "test-chart"
    os.environ["IMAGE_TAG"] = "invalid-format"  # Not starting with dev- or production-

    # Run CLI expecting SystemExit due to validation failure
    with pytest.raises(SystemExit) as exc_info:
        cli.main()

    # Verify exit code is 1
    assert exc_info.value.code == 1

    # Check error message
    captured = capsys.readouterr()
    assert "Error: Invalid IMAGE_TAG format: 'invalid-format'" in captured.out
    assert "Must start with 'dev-', 'production-', 'canary-' or be a valid semver" in captured.out

    # Verify no PRs were created
    assert mock_git_operations['create_pull_request'].call_count == 0

    # Verify no files were changed
    dev_tag_yaml = read_tag_yaml(
        base_dir / "dev-keboola-gcp-us-central1" / "test-chart" / "tag.yaml"
    )
    assert dev_tag_yaml["image"]["tag"] == "old-tag"


def test_invalid_extra_tag_format(cli_test_env, mock_git_operations, capsys):
    """Test error handling for invalid extra tag format.

    This test verifies that:
    1. Invalid extra tag format is detected
    2. The script exits with the correct error code
    3. Appropriate error message is displayed
    """
    base_dir, mock_repo, mock_github_repo = cli_test_env

    # Set environment variables with invalid extra tag format
    os.environ["HELM_CHART"] = "test-chart"
    os.environ["IMAGE_TAG"] = "dev-1.2.3"
    os.environ["EXTRA_TAG1"] = "invalid-format"  # Missing colon separator

    # Run CLI expecting an error
    with pytest.raises(SystemExit) as exc_info:
        cli.main()

    # Check error message
    captured = capsys.readouterr()
    assert "Error: EXTRA_TAG1 must be in format 'path:value'" in captured.out

    # Verify exit code
    assert exc_info.value.code == 1

    # Verify PR was not created
    assert mock_git_operations['create_pull_request'].call_count == 0


def test_valid_extra_tag_formats(cli_test_env, capsys):
    """Test valid extra tag formats including semver.

    This test verifies that:
    1. Extra tags with valid formats are accepted:
       - dev- prefix
       - production- prefix
       - semver format (0.1.2)
       - semver with v prefix (v0.1.2)
    2. Tag updates are processed correctly
    3. PRs are created as expected
    """
    base_dir, mock_repo, mock_github_repo = cli_test_env

    # Set environment variables with valid extra tag formats
    os.environ["HELM_CHART"] = "test-chart"
    os.environ["IMAGE_TAG"] = "dev-1.2.3"
    os.environ["EXTRA_TAG1"] = "path1:dev-1.2.3"  # Standard dev format
    os.environ["EXTRA_TAG2"] = "path2:1.2.3"  # Semver format without v

    # Track PRs
    created_prs = []

    def mock_create_branch_commit_and_pr(self, branch_name, files_to_commit, commit_message, pr_title, pr_body, base_branch="main", auto_merge=False):
        """Mock PR creation to track PR details."""
        created_prs.append({"branch": branch_name, "title": pr_title, "base": base_branch})
        print(f"Created PR: {pr_title} (branch: {branch_name}, base: {base_branch})")
        return "https://github.com/mock-org/mock-repo/pull/123"

    # Mock create_pr but use real config
    with patch("helm_image_updater.io_layer.IOLayer.create_branch_commit_and_pr", mock_create_branch_commit_and_pr):
        # Run CLI
        cli.main()

    # Check console output
    captured = capsys.readouterr()
    assert "Processing Helm chart: test-chart" in captured.out
    assert "Extra tags to update:" in captured.out
    assert "  - path1: dev-1.2.3" in captured.out
    assert "  - path2: 1.2.3" in captured.out

    # Verify PR was created (dev tag should trigger a PR for dev stacks)
    assert len(created_prs) == 1
    assert "test-chart" in created_prs[0]["title"]

    # Run another test with v-prefixed semver
    os.environ.clear()
    os.environ["GH_TOKEN"] = "fake-token"
    os.environ["HELM_CHART"] = "test-chart"
    os.environ["IMAGE_TAG"] = "production-1.2.3"
    os.environ["EXTRA_TAG1"] = "path1:v1.2.3"  # Semver format with v prefix

    created_prs.clear()

    # Mock create_pr but use real config
    with patch("helm_image_updater.io_layer.IOLayer.create_branch_commit_and_pr", mock_create_branch_commit_and_pr):
        # Run CLI
        cli.main()

    # Check console output
    captured = capsys.readouterr()
    assert "Processing Helm chart: test-chart" in captured.out
    assert "Extra tags to update:" in captured.out
    assert "  - path1: v1.2.3" in captured.out

    # Verify PR was created (production tag should trigger a PR for all stacks)
    assert len(created_prs) == 1
    assert "test-chart" in created_prs[0]["title"]


def test_nonexistent_stack_override(cli_test_env, capsys):
    """Test error handling for non-existent override stack.

    This test verifies that:
    1. Non-existent override stack is detected
    2. Console output correctly reports the error
    3. No files are modified
    4. No PRs are created
    """
    base_dir, mock_repo, mock_github_repo = cli_test_env

    # Set environment variables with non-existent override stack
    os.environ["HELM_CHART"] = "test-chart"
    os.environ["IMAGE_TAG"] = "dev-1.2.3"
    os.environ["OVERRIDE_STACK"] = "non-existent-stack"

    # Track PRs
    created_prs = []

    def mock_create_branch_commit_and_pr(self, branch_name, files_to_commit, commit_message, pr_title, pr_body, base_branch="main", auto_merge=False):
        created_prs.append({"branch": branch_name, "title": pr_title, "base": base_branch})

    # Only mock create_pr, use real config
    with patch("helm_image_updater.io_layer.IOLayer.create_branch_commit_and_pr", mock_create_branch_commit_and_pr):
        # Run CLI
        cli.main()

    # Check console output
    captured = capsys.readouterr()
    assert "Override stack: non-existent-stack" in captured.out
    assert "No stacks found for strategy override" in captured.out

    # Verify tag.yaml files were not modified
    dev_tag_yaml = read_tag_yaml(
        base_dir / "dev-keboola-gcp-us-central1" / "test-chart" / "tag.yaml"
    )
    assert dev_tag_yaml["image"]["tag"] == "old-tag"

    # Verify PR was not created
    assert len(created_prs) == 0


def test_multi_cloud_multi_stage_automerge_true(cli_test_env, capsys):
    """Test multi-cloud multi-stage deployment with automerge=true.

    This test verifies that:
    1. With multi-stage=true and automerge=true
    2. For production tags, it creates 6 PRs:
       - 3 Dev PRs with [multi-stage] [test sync {cloud}] that have automerge=true
       - 3 Prod PRs with [multi-stage] [prod sync {cloud}] that have automerge=false
    3. Each cloud (aws, azure, gcp) gets both dev and prod PRs
    4. The automerge setting for dev stacks is true, prod stacks is false
    """
    base_dir, mock_repo, mock_github_repo = cli_test_env

    # Set environment variables for multi-cloud multi-stage deployment
    os.environ["HELM_CHART"] = "test-chart"
    os.environ["IMAGE_TAG"] = "production-1.2.3"
    os.environ["MULTI_STAGE"] = "true"
    os.environ["AUTOMERGE"] = "true"

    # Track PRs with their automerge setting
    created_prs = []

    def mock_create_branch_commit_and_pr(self, branch_name, files_to_commit, commit_message, pr_title, pr_body, base_branch="main", auto_merge=False):
        created_prs.append(
            {
                "branch": branch_name,
                "title": pr_title,
                "base": base_branch,
                "automerge": auto_merge,
            }
        )
        print(
            f"Created PR: {pr_title} (branch: {branch_name}, base: {base_branch}, automerge: {auto_merge})"
        )

    # Mock create_pr but use real config to capture automerge setting
    with patch("helm_image_updater.io_layer.IOLayer.create_branch_commit_and_pr", mock_create_branch_commit_and_pr):
        # Run CLI
        cli.main()

    # Check console output
    captured = capsys.readouterr()
    assert "Multi-stage deployment: True" in captured.out
    assert "Automerge: True" in captured.out

    # Verify 6 PRs were created (3 dev + 3 prod)
    assert len(created_prs) == 6, f"Should create exactly 6 PRs, got {len(created_prs)}"

    # Debug: Print all PR titles for inspection
    print("DEBUG: All PR titles:")
    for pr in created_prs:
        print(f"  - '{pr['title']}' (automerge: {pr['automerge']})")

    # Find dev and prod PRs by cloud
    dev_prs = {}
    prod_prs = {}
    
    for pr in created_prs:
        if "[multi-stage] [test sync" in pr["title"]:
            # Extract cloud from title like "[multi-stage] [test sync aws]"
            for cloud in ["aws", "azure", "gcp"]:
                if f"[test sync {cloud}]" in pr["title"]:
                    dev_prs[cloud] = pr
                    break
        elif "[multi-stage] [prod sync" in pr["title"]:
            # Extract cloud from title like "[multi-stage] [prod sync aws]"
            for cloud in ["aws", "azure", "gcp"]:
                if f"[prod sync {cloud}]" in pr["title"]:
                    prod_prs[cloud] = pr
                    break

    # Verify all cloud PRs exist
    expected_clouds = ["aws", "azure", "gcp"]
    for cloud in expected_clouds:
        assert cloud in dev_prs, f"Should create dev PR for {cloud}"
        assert cloud in prod_prs, f"Should create prod PR for {cloud}"

    # Verify automerge settings for all clouds
    for cloud in expected_clouds:
        assert dev_prs[cloud]["automerge"] is True, f"Dev {cloud} PR should have automerge=True"
        assert prod_prs[cloud]["automerge"] is False, f"Prod {cloud} PR should have automerge=False"

    # Verify PR title patterns
    for cloud in expected_clouds:
        assert dev_prs[cloud]["title"].startswith(f"[multi-stage] [test sync {cloud}]"), (
            f"Dev {cloud} PR should start with [multi-stage] [test sync {cloud}]"
        )
        assert prod_prs[cloud]["title"].startswith(f"[multi-stage] [prod sync {cloud}]"), (
            f"Prod {cloud} PR should start with [multi-stage] [prod sync {cloud}]"
        )


def test_multi_cloud_multi_stage_automerge_false(cli_test_env, capsys):
    """Test multi-cloud multi-stage deployment with automerge=false.

    This test verifies that:
    1. With multi-stage=true and automerge=false
    2. For production tags, it creates 6 PRs:
       - 3 Dev PRs with [multi-stage] [test sync {cloud} manual] that respect automerge=false
       - 3 Prod PRs with [multi-stage] [prod sync {cloud}] that are NOT auto-merged
    3. Each cloud (aws, azure, gcp) gets both dev and prod PRs
    4. All PRs have automerge=false due to user preference and multi-stage prod rule
    """
    base_dir, mock_repo, mock_github_repo = cli_test_env

    # Set environment variables for multi-cloud multi-stage deployment with automerge=false
    os.environ["HELM_CHART"] = "test-chart"
    os.environ["IMAGE_TAG"] = "production-1.2.3"
    os.environ["MULTI_STAGE"] = "true"
    os.environ["AUTOMERGE"] = "false"

    # Track PRs with their automerge setting
    created_prs = []

    def mock_create_branch_commit_and_pr(self, branch_name, files_to_commit, commit_message, pr_title, pr_body, base_branch="main", auto_merge=False):
        created_prs.append(
            {
                "branch": branch_name,
                "title": pr_title,
                "base": base_branch,
                "automerge": auto_merge,
            }
        )
        print(
            f"Created PR: {pr_title} (branch: {branch_name}, base: {base_branch}, automerge: {auto_merge})"
        )

    # Mock create_pr but use real config to capture automerge setting
    with patch("helm_image_updater.io_layer.IOLayer.create_branch_commit_and_pr", mock_create_branch_commit_and_pr):
        # Run CLI
        cli.main()

    # Check console output
    captured = capsys.readouterr()
    assert "Multi-stage deployment: True" in captured.out
    assert "Automerge: False" in captured.out

    # Verify 6 PRs were created (3 dev + 3 prod)
    assert len(created_prs) == 6, f"Should create exactly 6 PRs, got {len(created_prs)}"

    # Debug: Print all PR titles for inspection
    print("DEBUG: All PR titles:")
    for pr in created_prs:
        print(f"  - '{pr['title']}' (automerge: {pr['automerge']})")

    # Find dev and prod PRs by cloud
    dev_prs = {}
    prod_prs = {}
    
    for pr in created_prs:
        if "[multi-stage] [test sync" in pr["title"]:
            # Extract cloud from title like "[multi-stage] [test sync aws manual]"
            for cloud in ["aws", "azure", "gcp"]:
                if f"[test sync {cloud} manual]" in pr["title"]:
                    dev_prs[cloud] = pr
                    break
        elif "[multi-stage] [prod sync" in pr["title"]:
            # Extract cloud from title like "[multi-stage] [prod sync aws]"
            for cloud in ["aws", "azure", "gcp"]:
                if f"[prod sync {cloud}]" in pr["title"]:
                    prod_prs[cloud] = pr
                    break

    # Verify all cloud PRs exist
    expected_clouds = ["aws", "azure", "gcp"]
    for cloud in expected_clouds:
        assert cloud in dev_prs, f"Should create dev PR for {cloud}"
        assert cloud in prod_prs, f"Should create prod PR for {cloud}"

    # Verify automerge settings for all clouds (all should be False)
    for cloud in expected_clouds:
        assert dev_prs[cloud]["automerge"] is False, f"Dev {cloud} PR should have automerge=False"
        assert prod_prs[cloud]["automerge"] is False, f"Prod {cloud} PR should have automerge=False"

    # Verify PR title patterns
    for cloud in expected_clouds:
        assert dev_prs[cloud]["title"].startswith(f"[multi-stage] [test sync {cloud} manual]"), (
            f"Dev {cloud} PR should start with [multi-stage] [test sync {cloud} manual]"
        )
        assert prod_prs[cloud]["title"].startswith(f"[multi-stage] [prod sync {cloud}]"), (
            f"Prod {cloud} PR should start with [multi-stage] [prod sync {cloud}]"
        )



def test_dry_run(cli_test_env, capsys):
    """Test dry run mode doesn't change files.

    This test verifies that:
    1. Dry run mode correctly reports what would be changed
    2. No actual file changes are made
    3. PRs are only simulated but not actually created
    """
    base_dir, mock_repo, mock_github_repo = cli_test_env

    # Set environment variables for dry run
    os.environ["HELM_CHART"] = "test-chart"
    os.environ["IMAGE_TAG"] = "dev-1.2.3-dry-run"
    os.environ["DRY_RUN"] = "true"

    # Run CLI without any mocks for PR creation - we want to validate that
    # create_pr is called but only in dry run mode
    cli.main()

    # Check console output
    captured = capsys.readouterr()
    assert "Dry run: True" in captured.out
    assert "[DRY RUN] Would write to" in captured.out

    # Verify dry run simulation correctly identifies changes
    assert "[DRY RUN] Would create PR:" in captured.out

    # Verify no tag.yaml files were actually changed
    dev_tag_yaml = read_tag_yaml(
        base_dir / "dev-keboola-gcp-us-central1" / "test-chart" / "tag.yaml"
    )
    assert dev_tag_yaml["image"]["tag"] == "old-tag"

    # Verify git commands were not called (which would happen if PR was actually created)
    assert not mock_repo.git.push.called, "Git push should not be called in dry run"
    assert not mock_github_repo.create_pull.called, (
        "GitHub create_pull should not be called in dry run"
    )


def test_custom_tag_with_override_stack(cli_test_env, capsys):
    """Test that custom tag formats can be used with override stack.

    This test verifies that:
    1. Non-standard tag formats (like connection-dev-tag-1) can be used when OVERRIDE_STACK is defined
    2. Only the specified stack is updated
    3. PR is created with the correct information
    """
    base_dir, mock_repo, mock_github_repo = cli_test_env

    # Set environment variables with custom tag and override stack
    os.environ["HELM_CHART"] = "test-chart"
    os.environ["IMAGE_TAG"] = "dev-tag-1"  # Non-standard tag format
    os.environ["OVERRIDE_STACK"] = (
        "dev-keboola-gcp-us-east1-e2e"  # Explicitly target a dev stack
    )

    # Track PRs
    created_prs = []

    def mock_create_branch_commit_and_pr(self, branch_name, files_to_commit, commit_message, pr_title, pr_body, base_branch="main", auto_merge=False):
        """Mock PR creation to track PR details."""
        created_prs.append({"branch": branch_name, "title": pr_title, "base": base_branch})
        print(f"Created PR: {pr_title} (branch: {branch_name}, base: {base_branch})")
        return "https://github.com/mock-org/mock-repo/pull/123"

    # Mock create_pr but use real config
    with patch("helm_image_updater.io_layer.IOLayer.create_branch_commit_and_pr", mock_create_branch_commit_and_pr):
        # Run CLI
        cli.main()

    # Check console output
    captured = capsys.readouterr()
    assert "Processing Helm chart: test-chart" in captured.out
    assert "Override stack: dev-keboola-gcp-us-east1-e2e" in captured.out

    # Verify tag.yaml was updated in the specified stack
    dev_tag_yaml = read_tag_yaml(
        base_dir / "dev-keboola-gcp-us-east1-e2e" / "test-chart" / "tag.yaml"
    )
    assert dev_tag_yaml["image"]["tag"] == "dev-tag-1"

    # Verify other stacks were NOT updated
    prod_tag_yaml = read_tag_yaml(
        base_dir / "com-keboola-gcp-prod" / "test-chart" / "tag.yaml"
    )
    assert prod_tag_yaml["image"]["tag"] == "old-tag"

    # Verify PR was created
    assert len(created_prs) == 1
    assert "test-chart" in created_prs[0]["title"]
    assert "dev-keboola-gcp-us-east1-e2e" in created_prs[0]["title"]


def test_dev_tag_with_production_override_stack(cli_test_env, capsys):
    """Test that dev tags cannot be used with production stack override.

    This test verifies that:
    1. When targeting a production stack with OVERRIDE_STACK
    2. Using a dev tag is rejected
    3. No files are changed
    4. No PRs are created
    """
    base_dir, mock_repo, mock_github_repo = cli_test_env

    # Set environment variables with dev tag and production stack override
    os.environ["HELM_CHART"] = "test-chart"
    os.environ["IMAGE_TAG"] = "dev-123-tag"  # Dev tag
    os.environ["OVERRIDE_STACK"] = "com-keboola-gcp-prod"  # Production stack

    # Track PRs
    created_prs = []

    def mock_create_branch_commit_and_pr(self, branch_name, files_to_commit, commit_message, pr_title, pr_body, base_branch="main", auto_merge=False):
        """Mock PR creation to track PR details."""
        created_prs.append({"branch": branch_name, "title": pr_title, "base": base_branch})
        print(f"Created PR: {pr_title} (branch: {branch_name}, base: {base_branch})")
        return "https://github.com/mock-org/mock-repo/pull/123"

    # Run CLI expecting an error due to validation
    with pytest.raises(SystemExit) as exc_info:
        cli.main()
    
    # Check error message
    captured = capsys.readouterr()
    assert "Error: Cannot apply non-production tag to production stack" in captured.out

    # Verify exit code
    assert exc_info.value.code == 1

    # Verify tag.yaml was NOT updated in the production stack
    prod_tag_yaml = read_tag_yaml(
        base_dir / "com-keboola-gcp-prod" / "test-chart" / "tag.yaml"
    )
    assert prod_tag_yaml["image"]["tag"] == "old-tag"

    # Verify no PR was created (no mock calls should have been made)
    assert len(created_prs) == 0


def test_happy_path_production_update(cli_test_env, mock_git_operations, capsys):
    """Test the most common happy path - production tag with automerge.

    This test verifies the complete flow for the most common production scenario:
    1. Production tag is applied to all stacks
    2. Automerge is enabled (default)
    3. Dry run is disabled (default)
    4. Git operations are performed correctly
    5. PR is created and auto-merged
    """
    base_dir, mock_repo, mock_github_repo = cli_test_env

    # Set environment variables for production update
    os.environ["HELM_CHART"] = "test-chart"
    os.environ["IMAGE_TAG"] = "production-2.0.0"
    os.environ["AUTOMERGE"] = "true"  # this is default, but being explicit
    # DRY_RUN is not set, which defaults to false

    # Track PR creation calls
    created_prs = []

    def track_pr_creation(*args, **kwargs):
        """Track GitHub PR creation details."""
        # Extract arguments (self, title, body, branch_name, base_branch="main", auto_merge=False)
        if len(args) >= 4:
            title, body, branch_name = args[1], args[2], args[3]
            base_branch = args[4] if len(args) > 4 else kwargs.get("base_branch", "main")
            auto_merge = args[5] if len(args) > 5 else kwargs.get("auto_merge", False)
        else:
            title = kwargs.get("title", "Unknown")
            body = kwargs.get("body", "")
            branch_name = kwargs.get("branch_name", "unknown-branch")
            base_branch = kwargs.get("base_branch", "main")
            auto_merge = kwargs.get("auto_merge", False)
        
        created_prs.append({
            "branch": branch_name,
            "title": title,
            "base": base_branch,
            "automerge": auto_merge,
        })

        # Simulate the non-dry-run behavior that would occur in create_pr
        if auto_merge:
            print(f"Created and auto-merged PR: {title} (branch: {branch_name}, base: {base_branch})")
        else:
            print(f"Created PR: {title} (branch: {branch_name}, base: {base_branch})")

        return "https://github.com/mock-org/mock-repo/pull/123"

    # Customize the PR creation mock to track calls
    mock_git_operations['create_pull_request'].side_effect = track_pr_creation
    
    # Run CLI - files will be written, Git/GitHub operations mocked
    cli.main()

    # Check console output
    captured = capsys.readouterr()
    assert "Processing Helm chart: test-chart" in captured.out
    assert "New image tag: production-2.0.0" in captured.out
    assert "New image tag:" in captured.out

    # Verify console output shows updates for both dev and prod stacks
    assert "Updated dev-keboola-gcp-us-central1/test-chart/tag.yaml: image.tag from old-tag to production-2.0.0" in captured.out
    assert "Updated com-keboola-gcp-prod/test-chart/tag.yaml: image.tag from old-tag to production-2.0.0" in captured.out

    # Verify Git operations were performed
    assert mock_git_operations['checkout_branch'].called, "git checkout should be called"
    assert mock_git_operations['add_files'].called, "git add should be called"
    assert mock_git_operations['commit'].called, "git commit should be called"
    assert mock_git_operations['create_pull_request'].called, "create PR should be called"

    # Verify our tracking shows PR was created with automerge enabled
    assert len(created_prs) == 1
    assert created_prs[0]["automerge"] is True
    assert "test-chart" in created_prs[0]["title"]


def test_semver_main_image_tag(cli_test_env, capsys):
    """Test that semver formats are accepted for the main IMAGE_TAG.

    This test verifies that:
    1. IMAGE_TAG can be a semver with or without v prefix (0.1.2 or v0.1.2)
    2. Semver tags are treated like production tags and update all stacks
    3. PRs are created with the correct information
    """
    base_dir, mock_repo, mock_github_repo = cli_test_env

    # Set environment variables with semver image tag (no v prefix)
    os.environ["HELM_CHART"] = "test-chart"
    os.environ["IMAGE_TAG"] = "1.2.3"  # Semver without v prefix

    # Track PRs
    created_prs = []

    def mock_create_branch_commit_and_pr(self, branch_name, files_to_commit, commit_message, pr_title, pr_body, base_branch="main", auto_merge=False):
        """Mock PR creation to track PR details."""
        created_prs.append({"branch": branch_name, "title": pr_title, "base": base_branch})
        print(f"Created PR: {pr_title} (branch: {branch_name}, base: {base_branch})")
        return "https://github.com/mock-org/mock-repo/pull/123"

    # Mock create_pr but use real config
    with patch("helm_image_updater.io_layer.IOLayer.create_branch_commit_and_pr", mock_create_branch_commit_and_pr):
        # Run CLI
        cli.main()

    # Check console output
    captured = capsys.readouterr()
    assert "Processing Helm chart: test-chart" in captured.out
    assert "New image tag: 1.2.3" in captured.out

    # Verify tag.yaml was updated in both dev and prod stacks (like production tag)
    dev_tag_yaml = read_tag_yaml(
        base_dir / "dev-keboola-gcp-us-central1" / "test-chart" / "tag.yaml"
    )
    assert dev_tag_yaml["image"]["tag"] == "1.2.3"

    prod_tag_yaml = read_tag_yaml(
        base_dir / "com-keboola-gcp-prod" / "test-chart" / "tag.yaml"
    )
    assert prod_tag_yaml["image"]["tag"] == "1.2.3"

    # Verify PR was created
    assert len(created_prs) == 1
    assert "test-chart" in created_prs[0]["title"]

    # Test with v-prefixed semver
    os.environ.clear()
    os.environ["GH_TOKEN"] = "fake-token"
    os.environ["HELM_CHART"] = "test-chart"
    os.environ["IMAGE_TAG"] = "v2.3.4"  # Semver with v prefix

    # Reset mocks and stacks
    created_prs.clear()

    # Mock create_pr but use real config
    with patch("helm_image_updater.io_layer.IOLayer.create_branch_commit_and_pr", mock_create_branch_commit_and_pr):
        # Run CLI
        cli.main()

    # Check console output
    captured = capsys.readouterr()
    assert "Processing Helm chart: test-chart" in captured.out
    assert "New image tag: v2.3.4" in captured.out

    # Verify tag.yaml was updated in both dev and prod stacks (like production tag)
    dev_tag_yaml = read_tag_yaml(
        base_dir / "dev-keboola-gcp-us-central1" / "test-chart" / "tag.yaml"
    )
    assert dev_tag_yaml["image"]["tag"] == "v2.3.4"

    prod_tag_yaml = read_tag_yaml(
        base_dir / "com-keboola-gcp-prod" / "test-chart" / "tag.yaml"
    )
    assert prod_tag_yaml["image"]["tag"] == "v2.3.4"

    # Verify PR was created
    assert len(created_prs) == 1
    assert "test-chart" in created_prs[0]["title"]


def test_canary_tag_in_extra_tag_should_update_canary_stack(cli_test_env, mock_git_operations, capsys):
    """Test that canary tag in EXTRA_TAG properly updates canary stack.

    When a canary tag is specified in an extra tag (EXTRA_TAG1 or EXTRA_TAG2),
    the system should:
    1. Detect it as a canary deployment
    2. Switch to the canary branch
    3. Update ONLY the canary stack with the extra tag value

    This test will FAIL initially and pass after the bug is fixed.
    """
    base_dir, mock_repo, mock_github_repo = cli_test_env

    # Setup mock repo to track git operations
    git_calls = []

    def track_git_call(*args, **kwargs):
        git_calls.append(args)
        return Mock()

    mock_repo.git.checkout = Mock(side_effect=track_git_call)
    mock_repo.git.pull = Mock(side_effect=track_git_call)
    mock_repo.active_branch = Mock()
    mock_repo.active_branch.name = "canary-orion"  # Simulate being on canary branch after switch

    # Mock create_pr to track PRs created
    created_prs = []

    def mock_create_branch_commit_and_pr(self, branch_name, files_to_commit, commit_message, pr_title, pr_body, base_branch="main", auto_merge=False):
        """Mock PR creation to track PR details."""
        created_prs.append({
            "branch": branch_name,
            "title": pr_title,
            "base": base_branch,
            "files": files_to_commit
        })
        return "https://github.com/mock-org/mock-repo/pull/123"

    # Test scenario: canary tag in EXTRA_TAG1 only (no IMAGE_TAG)
    os.environ["HELM_CHART"] = "test-chart"
    os.environ["EXTRA_TAG1"] = "image.tag:canary-orion-xyz789"  # Canary tag in extra tag
    os.environ["AUTOMERGE"] = "true"

    with patch("helm_image_updater.io_layer.IOLayer.create_branch_commit_and_pr", mock_create_branch_commit_and_pr):
        cli.main()

    # Check console output
    captured = capsys.readouterr()

    # ✅ EXPECTED BEHAVIOR: System should detect canary tag in extra tag
    assert "Detected canary tag, switching to branch 'canary-orion'" in captured.out, \
        "Should detect canary tag from EXTRA_TAG"
    assert "Successfully switched to branch 'canary-orion'" in captured.out, \
        "Should switch to canary branch"
    assert "Updating canary stack" in captured.out, \
        "Should update canary stack"

    # ✅ EXPECTED: Canary stack should be updated
    canary_tag_yaml = read_tag_yaml(
        base_dir / "dev-keboola-canary-orion" / "test-chart" / "tag.yaml"
    )
    assert canary_tag_yaml["image"]["tag"] == "canary-orion-xyz789", \
        "Canary stack should be updated with canary tag"

    # ✅ EXPECTED: Dev stacks should NOT be updated
    dev_tag_yaml = read_tag_yaml(
        base_dir / "dev-keboola-gcp-us-central1" / "test-chart" / "tag.yaml"
    )
    assert dev_tag_yaml["image"]["tag"] == "old-tag", \
        "Dev stacks should not be updated"

    # ✅ EXPECTED: PR should be created against canary branch
    assert len(created_prs) == 1, "Should create exactly one PR"
    assert created_prs[0]["base"] == "canary-orion", \
        "PR should target canary-orion branch"

    # ✅ EXPECTED: Only canary stack should be in PR files
    pr_files = created_prs[0]["files"]
    assert any("dev-keboola-canary-orion" in f for f in pr_files), \
        "Canary stack should be in PR"
    assert not any("dev-keboola-gcp-us-central1" in f for f in pr_files), \
        "Dev stacks should not be in PR"

    # ✅ EXPECTED: Git checkout to canary branch should have happened
    checkout_calls = [call for call in git_calls if "canary-orion" in str(call)]
    assert len(checkout_calls) >= 1, \
        "Should checkout canary-orion branch"
