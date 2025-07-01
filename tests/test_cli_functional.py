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
        patch("git.Repo", return_value=mock_repo),
        patch("helm_image_updater.cli.Repo", return_value=mock_repo),
        patch("github.Github", return_value=Mock(get_repo=lambda x: mock_github_repo)),
        patch(
            "helm_image_updater.cli.Github",
            return_value=Mock(get_repo=lambda x: mock_github_repo),
        ),
        patch("helm_image_updater.pr_manager.create_pr", return_value=None),
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
    """Create test stack structure with tag.yaml files."""
    # Create dev stack
    dev_stack = base_path / "dev-keboola-gcp-us-central1"
    dev_stack.mkdir()
    (dev_stack / "test-chart").mkdir()
    create_tag_yaml(dev_stack / "test-chart" / "tag.yaml", "old-tag")

    # Create production stack
    prod_stack = base_path / "com-keboola-prod"
    prod_stack.mkdir()
    (prod_stack / "test-chart").mkdir()
    create_tag_yaml(prod_stack / "test-chart" / "tag.yaml", "old-tag")

    # Create canary stack
    canary_stack = base_path / "dev-keboola-canary-orion"
    canary_stack.mkdir()
    (canary_stack / "test-chart").mkdir()
    create_tag_yaml(canary_stack / "test-chart" / "tag.yaml", "old-tag")

    # Create e2e dev stack
    e2e_dev_stack = base_path / "dev-keboola-gcp-us-east1-e2e"
    e2e_dev_stack.mkdir()
    (e2e_dev_stack / "test-chart").mkdir()
    create_tag_yaml(e2e_dev_stack / "test-chart" / "tag.yaml", "old-tag")


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


def test_dev_tag_update(cli_test_env, capsys):
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

    # Mock create_pr to track PRs created
    created_prs = []

    def mock_create_pr(config, branch_name, pr_title, base="main"):
        """Mock PR creation to track PR details."""
        created_prs.append({"branch": branch_name, "title": pr_title, "base": base})
        print(f"Created PR: {pr_title} (branch: {branch_name}, base: {base})")

    # Mock create_pr but use real config
    with patch("helm_image_updater.tag_updater.create_pr", mock_create_pr):
        # Run CLI
        cli.main()

    # Check console output
    captured = capsys.readouterr()
    assert "Processing Helm chart: test-chart" in captured.out
    assert "Updating dev stacks (dev- tag)" in captured.out

    # Verify tag.yaml was updated in dev stack
    dev_tag_yaml = read_tag_yaml(
        base_dir / "dev-keboola-gcp-us-central1" / "test-chart" / "tag.yaml"
    )
    assert dev_tag_yaml["image"]["tag"] == "dev-1.2.3"

    # Verify tag.yaml was NOT updated in prod stack
    prod_tag_yaml = read_tag_yaml(
        base_dir / "com-keboola-prod" / "test-chart" / "tag.yaml"
    )
    assert prod_tag_yaml["image"]["tag"] == "old-tag"

    # Verify PR was created
    assert len(created_prs) == 1
    assert "test-chart" in created_prs[0]["title"]


def test_production_tag_update(cli_test_env, capsys):
    """Test updating all stacks with a production tag.

    This test verifies that:
    1. All stacks are updated with production tags
    2. The tag.yaml files are correctly modified
    3. Console output correctly reports the updates
    """
    base_dir, mock_repo, mock_github_repo = cli_test_env

    # Set environment variables for production tag update
    os.environ["HELM_CHART"] = "test-chart"
    os.environ["IMAGE_TAG"] = "production-1.2.3"
    os.environ["AUTOMERGE"] = "true"

    # Mock create_pr to track PRs created
    created_prs = []

    def mock_create_pr(config, branch_name, pr_title, base="main"):
        """Mock PR creation to track PR details."""
        created_prs.append({"branch": branch_name, "title": pr_title, "base": base})
        print(f"Created PR: {pr_title} (branch: {branch_name}, base: {base})")

    # Mock create_pr but use real config
    with patch("helm_image_updater.tag_updater.create_pr", mock_create_pr):
        # Run CLI
        cli.main()

    # Check console output
    captured = capsys.readouterr()
    assert "Processing Helm chart: test-chart" in captured.out
    assert "Updating all stacks (production- tag)" in captured.out

    # Verify tag.yaml was updated in both dev and prod stacks
    dev_tag_yaml = read_tag_yaml(
        base_dir / "dev-keboola-gcp-us-central1" / "test-chart" / "tag.yaml"
    )
    assert dev_tag_yaml["image"]["tag"] == "production-1.2.3"

    prod_tag_yaml = read_tag_yaml(
        base_dir / "com-keboola-prod" / "test-chart" / "tag.yaml"
    )
    assert prod_tag_yaml["image"]["tag"] == "production-1.2.3"

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

    def mock_create_pr(config, branch_name, pr_title, base="main"):
        """Mock PR creation to track PR details."""
        created_prs.append({"branch": branch_name, "title": pr_title, "base": base})
        print(f"Created PR: {pr_title} (branch: {branch_name}, base: {base})")

    # Test Case 1: Regular service that exists in multiple environments
    os.environ["HELM_CHART"] = "test-chart"
    os.environ["IMAGE_TAG"] = "canary-orion-1.2.3"
    os.environ["AUTOMERGE"] = "true"

    with patch("helm_image_updater.tag_updater.create_pr", mock_create_pr):
        cli.main()

    # Check console output for branch switching
    captured = capsys.readouterr()
    assert "Processing Helm chart: test-chart" in captured.out
    assert "Detected canary tag with prefix 'canary-orion'" in captured.out
    assert "switching to branch 'canary-orion'" in captured.out
    assert "Successfully switched to branch 'canary-orion'" in captured.out
    assert "Current branch: canary-orion" in captured.out
    assert "Updating canary stack" in captured.out

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
        base_dir / "com-keboola-prod" / "test-chart" / "tag.yaml"
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

    with patch("helm_image_updater.tag_updater.create_pr", mock_create_pr):
        cli.main()

    # Check console output shows proper branch switching before file checks
    captured = capsys.readouterr()
    assert "Processing Helm chart: metastore" in captured.out
    assert "Detected canary tag with prefix 'canary-orion'" in captured.out
    assert "switching to branch 'canary-orion'" in captured.out
    assert "Successfully switched to branch 'canary-orion'" in captured.out

    # Most importantly: verify it didn't exit early due to missing files
    assert (
        "tag.yaml for chart metastore does not exist in any stack" not in captured.out
    )
    assert "Updating canary stack" in captured.out

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


def test_missing_required_env_var(cli_test_env, capsys):
    """Test error handling for missing environment variables.

    This test verifies that:
    1. Missing HELM_CHART env var is detected
    2. The script raises a KeyError
    3. No PRs are created
    """
    base_dir, mock_repo, mock_github_repo = cli_test_env

    # Don't set HELM_CHART
    os.environ["IMAGE_TAG"] = "dev-1.2.3"

    # Track PRs
    created_prs = []

    def mock_create_pr(config, branch_name, pr_title, base="main"):
        created_prs.append({"branch": branch_name, "title": pr_title, "base": base})

    # Run CLI expecting an KeyError
    with (
        pytest.raises(KeyError) as e,
        patch("helm_image_updater.tag_updater.create_pr", mock_create_pr),
    ):
        cli.main()

    # Verify the missing key is HELM_CHART
    assert str(e.value) == "'HELM_CHART'"

    # Verify PR was not created
    assert len(created_prs) == 0


def test_invalid_tag_format(cli_test_env, capsys):
    """Test error handling for invalid tag format.

    This test verifies that:
    1. Invalid tag format is detected
    2. The script exits with the correct error code
    3. Appropriate error message is displayed
    4. No PRs are created
    """
    base_dir, mock_repo, mock_github_repo = cli_test_env

    # Set environment variables with invalid tag
    os.environ["HELM_CHART"] = "test-chart"
    os.environ["IMAGE_TAG"] = "invalid-format"  # Not starting with dev- or production-

    # Track PRs
    created_prs = []

    def mock_create_pr(config, branch_name, pr_title, base="main"):
        created_prs.append({"branch": branch_name, "title": pr_title, "base": base})

    # Run CLI expecting an error
    with (
        pytest.raises(SystemExit) as e,
        patch("helm_image_updater.tag_updater.create_pr", mock_create_pr),
    ):
        cli.main()

    # Check error message
    captured = capsys.readouterr()
    assert "Invalid image tag format" in captured.out

    # Verify exit code
    assert e.value.code == 1

    # Verify no files were changed
    dev_tag_yaml = read_tag_yaml(
        base_dir / "dev-keboola-gcp-us-central1" / "test-chart" / "tag.yaml"
    )
    assert dev_tag_yaml["image"]["tag"] == "old-tag"

    # Verify PR was not created
    assert len(created_prs) == 0


def test_invalid_extra_tag_format(cli_test_env, capsys):
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

    # Track PRs
    created_prs = []

    def mock_create_pr(config, branch_name, pr_title, base="main"):
        created_prs.append({"branch": branch_name, "title": pr_title, "base": base})

    # Run CLI expecting an error
    with (
        pytest.raises(SystemExit) as e,
        patch("helm_image_updater.tag_updater.create_pr", mock_create_pr),
    ):
        cli.main()

    # Check error message
    captured = capsys.readouterr()
    assert "EXTRA_TAG1 must be in format" in captured.out

    # Verify exit code
    assert e.value.code == 1

    # Verify PR was not created
    assert len(created_prs) == 0


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

    def mock_create_pr(config, branch_name, pr_title, base="main"):
        """Mock PR creation to track PR details."""
        created_prs.append({"branch": branch_name, "title": pr_title, "base": base})
        print(f"Created PR: {pr_title} (branch: {branch_name}, base: {base})")

    # Mock create_pr but use real config
    with patch("helm_image_updater.tag_updater.create_pr", mock_create_pr):
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
    with patch("helm_image_updater.tag_updater.create_pr", mock_create_pr):
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

    def mock_create_pr(config, branch_name, pr_title, base="main"):
        created_prs.append({"branch": branch_name, "title": pr_title, "base": base})

    # Only mock create_pr, use real config
    with patch("helm_image_updater.tag_updater.create_pr", mock_create_pr):
        # Run CLI
        cli.main()

    # Check console output
    captured = capsys.readouterr()
    assert "Stack non-existent-stack does not exist" in captured.out

    # Verify tag.yaml files were not modified
    dev_tag_yaml = read_tag_yaml(
        base_dir / "dev-keboola-gcp-us-central1" / "test-chart" / "tag.yaml"
    )
    assert dev_tag_yaml["image"]["tag"] == "old-tag"

    # Verify PR was not created
    assert len(created_prs) == 0


def test_multi_stage_automerge_true(cli_test_env, capsys):
    """Test multi-stage deployment with automerge=true.

    This test verifies that:
    1. With multi-stage=true and automerge=true
    2. For production tags, it creates:
       - Dev PR with [multi-stage] [test sync] that has automerge=true
       - Prod PR with [multi-stage] [prod sync] that has automerge=false
    3. The automerge setting for dev stacks is true (as requested)
    4. The automerge setting for prod stacks is forced to false (regardless of input)
    5. The prod PR title uses regular [prod sync] format when automerge=true is requested
       (This allows workflow to find it even though the PR itself won't auto-merge)
    """
    base_dir, mock_repo, mock_github_repo = cli_test_env

    # Set environment variables for multi-stage deployment
    os.environ["HELM_CHART"] = "test-chart"
    os.environ["IMAGE_TAG"] = "production-1.2.3"
    os.environ["MULTI_STAGE"] = "true"
    os.environ["AUTOMERGE"] = "true"

    # Track PRs with their automerge setting
    created_prs = []

    def mock_create_pr(config, branch_name, pr_title, base="main"):
        created_prs.append(
            {
                "branch": branch_name,
                "title": pr_title,
                "base": base,
                "automerge": config.automerge,
            }
        )
        print(
            f"Created PR: {pr_title} (branch: {branch_name}, base: {base}, automerge: {config.automerge})"
        )

    # Mock create_pr but use real config to capture automerge setting
    with patch("helm_image_updater.tag_updater.create_pr", mock_create_pr):
        # Run CLI
        cli.main()

    # Check console output
    captured = capsys.readouterr()
    assert "Multi-stage deployment: True" in captured.out
    assert "Automerge: True" in captured.out

    # Verify 2 PRs were created
    assert len(created_prs) == 2, "Should create exactly 2 PRs"

    # Debug: Print all PR titles for inspection
    print("DEBUG: All PR titles:")
    for pr in created_prs:
        print(f"  - '{pr['title']}' (automerge: {pr['automerge']})")

    # Find dev and prod PRs
    dev_pr = next(
        (pr for pr in created_prs if "[multi-stage] [test sync]" in pr["title"]), None
    )
    prod_pr = next(
        (pr for pr in created_prs if "[multi-stage] [prod sync]" in pr["title"]), None
    )

    # Verify PRs exist with correct settings
    assert dev_pr is not None, "Should create a dev PR with [multi-stage] [test sync]"
    assert prod_pr is not None, "Should create a prod PR with [multi-stage] [prod sync]"

    # Verify automerge settings
    assert dev_pr["automerge"] is True, "Dev PR should have automerge=True"
    assert prod_pr["automerge"] is False, (
        "Prod PR should have automerge=False (forced by multi-stage)"
    )

    # Verify complete PR title prefixes
    assert dev_pr["title"].startswith("[multi-stage] [test sync]"), (
        "Dev PR should start with [multi-stage] [test sync]"
    )
    assert prod_pr["title"].startswith("[multi-stage] [prod sync]"), (
        "Prod PR should start with [multi-stage] [prod sync]"
    )


def test_multi_stage_with_automerge_false(cli_test_env, capsys):
    """Test multi-stage deployment with automerge=false.

    This test verifies that:
    1. With multi-stage=true and automerge=false
    2. For production tags, it creates:
       - Dev PR with [multi-stage] [test sync manual] that respects the automerge=false setting
       - Prod PR with [multi-stage] [prod sync manual] that is NOT auto-merged
    3. The automerge setting for dev stacks respects user's setting
    4. The automerge setting for prod stacks is always forced to false
    5. The PR titles use different formats that won't match automated workflows
       (This prevents automated workflows from finding these PRs)
    """
    base_dir, mock_repo, mock_github_repo = cli_test_env

    # Set environment variables for multi-stage deployment with automerge=false
    os.environ["HELM_CHART"] = "test-chart"
    os.environ["IMAGE_TAG"] = "production-1.2.3"
    os.environ["MULTI_STAGE"] = "true"
    os.environ["AUTOMERGE"] = "false"

    # Track PRs with their automerge setting
    created_prs = []

    def mock_create_pr(config, branch_name, pr_title, base="main"):
        created_prs.append(
            {
                "branch": branch_name,
                "title": pr_title,
                "base": base,
                "automerge": config.automerge,
            }
        )
        print(
            f"Created PR: {pr_title} (branch: {branch_name}, base: {base}, automerge: {config.automerge})"
        )

    # Mock create_pr but use real config to capture automerge setting
    with patch("helm_image_updater.tag_updater.create_pr", mock_create_pr):
        # Run CLI
        cli.main()

    # Check console output
    captured = capsys.readouterr()
    assert "Multi-stage deployment: True" in captured.out
    assert "Automerge: False" in captured.out

    # Verify 2 PRs were created
    assert len(created_prs) == 2, "Should create exactly 2 PRs"

    # Debug: Print all PR titles for inspection
    print("DEBUG: All PR titles:")
    for pr in created_prs:
        print(f"  - '{pr['title']}' (automerge: {pr['automerge']})")

    # Find dev and prod PRs
    dev_pr = next(
        (pr for pr in created_prs if "[multi-stage] [test sync manual]" in pr["title"]),
        None,
    )
    prod_pr = next(
        (pr for pr in created_prs if "[multi-stage] [prod sync manual]" in pr["title"]),
        None,
    )

    # Verify PRs exist with correct settings
    assert dev_pr is not None, (
        "Should create a dev PR with [multi-stage] [test sync manual]"
    )
    assert prod_pr is not None, (
        "Should create a prod PR with [multi-stage] [prod sync manual]"
    )

    # Verify automerge settings
    assert dev_pr["automerge"] is False, "Dev PR should have automerge=False"
    assert prod_pr["automerge"] is False, "Prod PR should have automerge=False"

    # Verify complete PR title prefixes
    assert dev_pr["title"].startswith("[multi-stage] [test sync manual]"), (
        "Dev PR should start with [multi-stage] [test sync manual]"
    )
    assert prod_pr["title"].startswith("[multi-stage] [prod sync manual]"), (
        "Prod PR should start with [multi-stage] [prod sync manual]"
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
    assert "Would update" in captured.out

    # Verify the simulated PR creation was reported
    assert "Would create PR:" in captured.out

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

    def mock_create_pr(config, branch_name, pr_title, base="main"):
        """Mock PR creation to track PR details."""
        created_prs.append({"branch": branch_name, "title": pr_title, "base": base})
        print(f"Created PR: {pr_title} (branch: {branch_name}, base: {base})")

    # Mock create_pr but use real config
    with patch("helm_image_updater.tag_updater.create_pr", mock_create_pr):
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
        base_dir / "com-keboola-prod" / "test-chart" / "tag.yaml"
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
    os.environ["OVERRIDE_STACK"] = "com-keboola-prod"  # Production stack

    # Track PRs
    created_prs = []

    def mock_create_pr(config, branch_name, pr_title, base="main"):
        """Mock PR creation to track PR details."""
        created_prs.append({"branch": branch_name, "title": pr_title, "base": base})
        print(f"Created PR: {pr_title} (branch: {branch_name}, base: {base})")

    # Mock create_pr but use real config
    with patch("helm_image_updater.tag_updater.create_pr", mock_create_pr):
        # Run CLI
        cli.main()

    # Check console output
    captured = capsys.readouterr()
    assert "Processing Helm chart: test-chart" in captured.out
    assert "Cannot apply non-production tag to production stack" in captured.out

    # Verify tag.yaml was NOT updated in the production stack
    prod_tag_yaml = read_tag_yaml(
        base_dir / "com-keboola-prod" / "test-chart" / "tag.yaml"
    )
    assert prod_tag_yaml["image"]["tag"] == "old-tag"

    # Verify no PR was created
    assert len(created_prs) == 0


def test_happy_path_production_update(cli_test_env, capsys):
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

    # Set up mock repo to track git operations
    mock_repo.git.reset_mock()

    # Set up mock PR that simulates a successful PR creation and merge
    mock_pr = MagicMock()
    mock_pr.html_url = "https://github.com/mock-org/mock-repo/pull/999"
    mock_pr.mergeable = True  # PR is mergeable
    mock_github_repo.create_pull.return_value = mock_pr

    # Track PR creation calls
    created_prs = []

    def mock_create_pr(config, branch_name, pr_title, base="main"):
        """Mock PR creation with auto-merge functionality."""
        created_prs.append(
            {
                "branch": branch_name,
                "title": pr_title,
                "base": base,
                "automerge": config.automerge,
            }
        )

        # Simulate the non-dry-run behavior that would occur in create_pr
        config.repo.git.push("origin", branch_name)
        pr = config.github_repo.create_pull(
            title=pr_title,
            body="Mock PR body",
            head=branch_name,
            base=base,
        )

        # Auto-merge if configured
        if config.automerge:
            pr.merge()
            print(
                f"Created and auto-merged PR: {pr_title} (branch: {branch_name}, base: {base})"
            )
        else:
            print(f"Created PR: {pr_title} (branch: {branch_name}, base: {base})")

        return pr

    # Mock create_pr function
    with patch("helm_image_updater.tag_updater.create_pr", mock_create_pr):
        # Run CLI
        cli.main()

    # Check console output
    captured = capsys.readouterr()
    assert "Processing Helm chart: test-chart" in captured.out
    assert "New image tag: production-2.0.0" in captured.out
    assert "Updating all stacks (production- tag)" in captured.out

    # Verify tag.yaml was updated in both dev and prod stacks
    dev_tag_yaml = read_tag_yaml(
        base_dir / "dev-keboola-gcp-us-central1" / "test-chart" / "tag.yaml"
    )
    assert dev_tag_yaml["image"]["tag"] == "production-2.0.0"

    prod_tag_yaml = read_tag_yaml(
        base_dir / "com-keboola-prod" / "test-chart" / "tag.yaml"
    )
    assert prod_tag_yaml["image"]["tag"] == "production-2.0.0"

    # Verify Git operations were performed
    assert mock_repo.git.checkout.called, "git checkout should be called"
    assert mock_repo.git.add.called, "git add should be called"
    assert mock_repo.git.commit.called, "git commit should be called"
    assert mock_repo.git.push.called, "git push should be called"

    # Verify PR was created with correct parameters
    assert mock_github_repo.create_pull.called, "create_pull should be called"
    call_args = mock_github_repo.create_pull.call_args[1]
    assert "test-chart" in call_args["title"]
    assert call_args["base"] == "main"

    # Verify PR auto-merge was attempted
    assert mock_pr.merge.called, "PR merge should be called for auto-merge"

    # Verify our tracking shows PR was created with automerge enabled
    assert len(created_prs) == 1
    assert created_prs[0]["automerge"] is True


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

    def mock_create_pr(config, branch_name, pr_title, base="main"):
        """Mock PR creation to track PR details."""
        created_prs.append({"branch": branch_name, "title": pr_title, "base": base})
        print(f"Created PR: {pr_title} (branch: {branch_name}, base: {base})")

    # Mock create_pr but use real config
    with patch("helm_image_updater.tag_updater.create_pr", mock_create_pr):
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
        base_dir / "com-keboola-prod" / "test-chart" / "tag.yaml"
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
    with patch("helm_image_updater.tag_updater.create_pr", mock_create_pr):
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
        base_dir / "com-keboola-prod" / "test-chart" / "tag.yaml"
    )
    assert prod_tag_yaml["image"]["tag"] == "v2.3.4"

    # Verify PR was created
    assert len(created_prs) == 1
    assert "test-chart" in created_prs[0]["title"]
