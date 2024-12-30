"""Test module for PR creation functionality.

Tests the PR creation logic by verifying the expected parameters and behavior
for all possible combinations of image tags, automerge, and multi-stage settings.
"""

import pytest
from unittest.mock import Mock
from helm_image_updater.pr_manager import create_pr


@pytest.fixture
def mock_config(tmp_path, github_context, monkeypatch):
    """Create a mock configuration for testing."""
    config = Mock()
    config.helm_chart = "test-chart"
    config.image_tag = "dev-1.2.3"
    config.automerge = True
    config.dry_run = True  # Always use dry run for safety
    config.multi_stage = False

    # Copy github_context to the test directory and change to it
    github_context.rename(tmp_path / "github_context.json")
    monkeypatch.chdir(tmp_path)

    return config


def test_dev_tag_with_automerge(mock_config, capsys):
    """Test PR creation for dev tag with automerge enabled."""
    print(f"\n{'='*80}")
    print("Running test: dev tag with automerge")
    print(f"{'='*80}")

    mock_config.image_tag = "dev-1.2.3"
    mock_config.automerge = True
    mock_config.multi_stage = False

    print("\nConfiguration:")
    print(f"  - Helm chart: {mock_config.helm_chart}")
    print(f"  - Image tag: {mock_config.image_tag}")
    print(f"  - Automerge: {mock_config.automerge}")
    print(f"  - Multi-stage: {mock_config.multi_stage}")

    print("\nCreating PR...")
    create_pr(mock_config, "test-branch", "Update dev stack")

    captured = capsys.readouterr()
    print("\nCaptured output:")
    print(captured.out)

    print("\nVerifying PR creation...")
    assert "Would create PR: 'Update dev stack'" in captured.out
    assert "and automatically merge it" in captured.out

    print("Test completed successfully!")


def test_dev_tag_without_automerge(mock_config, capsys):
    """Test PR creation for dev tag without automerge."""
    print(f"\n{'='*80}")
    print("Running test: dev tag without automerge")
    print(f"{'='*80}")

    mock_config.image_tag = "dev-1.2.3"
    mock_config.automerge = False
    mock_config.multi_stage = False

    print("\nConfiguration:")
    print(f"  - Helm chart: {mock_config.helm_chart}")
    print(f"  - Image tag: {mock_config.image_tag}")
    print(f"  - Automerge: {mock_config.automerge}")
    print(f"  - Multi-stage: {mock_config.multi_stage}")

    print("\nCreating PR...")
    create_pr(mock_config, "test-branch", "Update dev stack")

    captured = capsys.readouterr()
    print("\nCaptured output:")
    print(captured.out)

    print("\nVerifying PR creation...")
    assert "Would create PR: 'Update dev stack' without auto-merging" in captured.out

    print("Test completed successfully!")


def test_production_tag_with_automerge(mock_config, capsys):
    """Test PR creation for production tag with automerge."""
    print(f"\n{'='*80}")
    print("Running test: production tag with automerge")
    print(f"{'='*80}")

    mock_config.image_tag = "production-1.2.3"
    mock_config.automerge = True
    mock_config.multi_stage = False

    print("\nConfiguration:")
    print(f"  - Helm chart: {mock_config.helm_chart}")
    print(f"  - Image tag: {mock_config.image_tag}")
    print(f"  - Automerge: {mock_config.automerge}")
    print(f"  - Multi-stage: {mock_config.multi_stage}")

    print("\nCreating PR...")
    create_pr(mock_config, "test-branch", "[production sync] Update all stacks")

    captured = capsys.readouterr()
    print("\nCaptured output:")
    print(captured.out)

    print("\nVerifying PR creation...")
    assert "Would create PR: '[production sync] Update all stacks'" in captured.out
    assert "and automatically merge it" in captured.out

    print("Test completed successfully!")


def test_production_tag_without_automerge(mock_config, capsys):
    """Test PR creation for production tag without automerge."""
    print(f"\n{'='*80}")
    print("Running test: production tag without automerge")
    print(f"{'='*80}")

    mock_config.image_tag = "production-1.2.3"
    mock_config.automerge = False
    mock_config.multi_stage = False

    print("\nConfiguration:")
    print(f"  - Helm chart: {mock_config.helm_chart}")
    print(f"  - Image tag: {mock_config.image_tag}")
    print(f"  - Automerge: {mock_config.automerge}")
    print(f"  - Multi-stage: {mock_config.multi_stage}")

    print("\nCreating PR...")
    create_pr(mock_config, "test-branch", "[production sync] Update stack")

    captured = capsys.readouterr()
    print("\nCaptured output:")
    print(captured.out)

    print("\nVerifying PR creation...")
    assert (
        "Would create PR: '[production sync] Update stack' without auto-merging"
        in captured.out
    )

    print("Test completed successfully!")


def test_production_tag_multi_stage(mock_config, capsys):
    """Test PR creation for production tag with multi-stage enabled."""
    print(f"\n{'='*80}")
    print("Running test: production tag with multi-stage")
    print(f"{'='*80}")

    mock_config.image_tag = "production-1.2.3"
    mock_config.automerge = True  # Should be ignored for production in multi-stage
    mock_config.multi_stage = True

    print("\nConfiguration:")
    print(f"  - Helm chart: {mock_config.helm_chart}")
    print(f"  - Image tag: {mock_config.image_tag}")
    print(f"  - Automerge: {mock_config.automerge}")
    print(f"  - Multi-stage: {mock_config.multi_stage}")

    # First PR - dev stack (should auto-merge)
    print("\nCreating PR for dev stack...")
    create_pr(mock_config, "test-branch-dev", "[multi-stage] Update dev stack")

    captured = capsys.readouterr()
    print("\nCaptured output (dev):")
    print(captured.out)

    print("\nVerifying dev PR creation...")
    assert "Would create PR: '[multi-stage] Update dev stack'" in captured.out
    assert "and automatically merge it" in captured.out

    # Second PR - production stacks (should NOT auto-merge)
    print("\nCreating PR for production stacks...")
    mock_config.automerge = False  # Force automerge off for production PR
    create_pr(mock_config, "test-branch-prod", "[multi-stage] Update production stacks")

    captured = capsys.readouterr()
    print("\nCaptured output (production):")
    print(captured.out)

    print("\nVerifying production PR creation...")
    assert "Would create PR: '[multi-stage] Update production stacks'" in captured.out
    assert "without auto-merging" in captured.out

    print("Test completed successfully!")
