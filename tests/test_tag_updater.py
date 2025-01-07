"""Test module for tag update strategies.

Tests the different update scenarios for dev and production tags,
including auto-merge and multi-stage deployment cases.
"""

import os
from unittest.mock import Mock, patch
import pytest
from helm_image_updater.config import UpdateConfig
from helm_image_updater.tag_updater import (
    handle_dev_tag,
    handle_production_tag,
    update_tag_yaml,
)


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
def mock_metadata():
    """Provides mock trigger metadata."""
    return {
        "source": {
            "repository": "test-repo",
            "repository_url": "https://github.com/test/repo",
            "sha": "abcdef1234567890abcdef1234567890abcdef12",
        }
    }


@pytest.fixture
def test_stacks(tmp_path):
    """Creates test stack structure with tag.yaml files."""
    # Create dev stack
    dev_stack = tmp_path / "dev-keboola-gcp-us-central1"
    dev_stack.mkdir()
    (dev_stack / "test-chart").mkdir()
    create_tag_yaml(dev_stack / "test-chart" / "tag.yaml", "old-tag")

    # Create production stacks
    com_stack = tmp_path / "com-keboola-prod"
    com_stack.mkdir()
    (com_stack / "test-chart").mkdir()
    create_tag_yaml(com_stack / "test-chart" / "tag.yaml", "old-tag")

    cloud_stack = tmp_path / "cloud-keboola-prod"
    cloud_stack.mkdir()
    (cloud_stack / "test-chart").mkdir()
    create_tag_yaml(cloud_stack / "test-chart" / "tag.yaml", "old-tag")

    return {
        "base_dir": tmp_path,
        "dev_stack": dev_stack,
        "com_stack": com_stack,
        "cloud_stack": cloud_stack,
    }


def create_tag_yaml(path, tag):
    """Helper to create tag.yaml files."""
    with open(path, "w", encoding="utf-8") as f:
        f.write(
            f"""image:
  tag: {tag}
"""
        )


@pytest.mark.parametrize(
    "test_case",
    [
        {
            "name": "dev tag with automerge",
            "image_tag": "dev-1.2.3",
            "automerge": True,
            "multi_stage": False,
            "expected_pr_count": 1,
            "expected_stacks": ["dev-keboola-gcp-us-central1"],
        },
        {
            "name": "dev tag without automerge",
            "image_tag": "dev-1.2.3",
            "automerge": False,
            "multi_stage": False,
            "expected_pr_count": 1,
            "expected_stacks": ["dev-keboola-gcp-us-central1"],
        },
        {
            "name": "production tag with automerge",
            "image_tag": "production-1.2.3",
            "automerge": True,
            "multi_stage": False,
            "expected_pr_count": 1,
            "expected_stacks": ["com-keboola-prod", "cloud-keboola-prod"],
        },
        {
            "name": "production tag without automerge",
            "image_tag": "production-1.2.3",
            "automerge": False,
            "multi_stage": False,
            "expected_pr_count": 2,  # One per production stack
            "expected_stacks": ["com-keboola-prod", "cloud-keboola-prod"],
        },
        {
            "name": "production tag with multi-stage",
            "image_tag": "production-1.2.3",
            "automerge": True,  # Should be ignored for prod stacks
            "multi_stage": True,
            "expected_pr_count": 2,  # One for dev, one for prod
            "expected_stacks": [
                "dev-keboola-gcp-us-central1",
                "com-keboola-prod",
                "cloud-keboola-prod",
            ],
        },
    ],
)
def test_tag_update_strategy(
    test_stacks, mock_repo, mock_github_repo, test_case, monkeypatch, github_context
):
    """Tests different tag update scenarios."""
    print(f"\n{'='*80}")
    print(f"Running test case: {test_case['name']}")
    print(f"{'='*80}")
    print("Configuration:")
    print(f"  - Image tag: {test_case['image_tag']}")
    print(f"  - Automerge: {test_case['automerge']}")
    print(f"  - Multi-stage: {test_case['multi_stage']}")
    print(f"  - Expected stacks: {test_case['expected_stacks']}")
    print(f"  - Expected PR count: {test_case['expected_pr_count']}")

    # Change to test directory and copy github context
    monkeypatch.chdir(test_stacks["base_dir"])
    github_context.rename(test_stacks["base_dir"] / "github_context.json")
    print(f"\nWorking directory: {test_stacks['base_dir']}")
    print(f"GitHub context file: {test_stacks['base_dir'] / 'github_context.json'}")

    config = UpdateConfig(
        repo=mock_repo,
        github_repo=mock_github_repo,
        helm_chart="test-chart",
        image_tag=test_case["image_tag"],
        automerge=test_case["automerge"],
        multi_stage=test_case["multi_stage"],
        dry_run=False,
    )

    # Mock os.listdir to return only the expected stacks
    def mock_listdir(path):
        if path == ".":
            result = test_case["expected_stacks"]
            print(f"\nListing directory '{path}':")
            for stack in result:
                print(f"  - {stack}")
            return result
        return []

    with patch("os.listdir", mock_listdir):
        print("\nExecuting update...")
        # Run the appropriate handler based on tag type
        if test_case["image_tag"].startswith("dev-"):
            print("Using dev tag handler")
            changes, missing = handle_dev_tag(config)
        else:
            print("Using production tag handler")
            changes, missing = handle_production_tag(config)

        print("\nResults:")
        print(f"Changes detected: {len(changes)}")
        for change in changes:
            print(f"  - Stack: {change['stack']}")
            print(f"    Chart: {change['chart']}")
            print(f"    Tag: {change['tag']}")
            print(f"    Automerge: {change['automerge']}")

        if missing:
            print("\nMissing tag.yaml files:")
            for miss in missing:
                print(f"  - {miss}")

        # Verify the changes
        filtered_changes = [
            change
            for change in changes
            if any(
                change["stack"].startswith(stack_prefix)
                for stack_prefix in test_case["expected_stacks"]
            )
        ]

        print("\nVerification:")
        print(f"Expected changes: {len(test_case['expected_stacks'])}")
        print(f"Actual changes: {len(filtered_changes)}")

        assert len(filtered_changes) == len(
            test_case["expected_stacks"]
        ), f"Expected {len(test_case['expected_stacks'])} changes, got {len(filtered_changes)}"

        # Verify the correct stacks were updated
        changed_stacks = {change["stack"] for change in filtered_changes}
        expected_stacks = set(test_case["expected_stacks"])

        print("\nStack comparison:")
        print("Expected stacks:")
        for stack in expected_stacks:
            print(f"  - {stack}")
        print("Actually changed stacks:")
        for stack in changed_stacks:
            print(f"  - {stack}")

        assert (
            changed_stacks == expected_stacks
        ), f"Expected stacks {expected_stacks}, got {changed_stacks}"

        print("\nTest completed successfully!")


def test_update_tag_yaml_with_extra_tags(test_stacks):
    """Tests updating tag.yaml with extra tags."""
    extra_tags = [
        {"path": "agent.image.tag", "value": "dev-1.0.0"},
        {"path": "sidecar.tag", "value": "production-2.0.0"},
    ]

    result = update_tag_yaml(
        test_stacks["dev_stack"], "test-chart", "dev-1.2.3", extra_tags=extra_tags
    )

    assert result is True

    # Verify the changes
    with open(test_stacks["dev_stack"] / "test-chart" / "tag.yaml") as f:
        data = f.read()
        assert "dev-1.2.3" in data
        assert "dev-1.0.0" in data
        assert "production-2.0.0" in data


def test_missing_tag_yaml(test_stacks):
    """Tests handling of missing tag.yaml files."""
    # Remove tag.yaml
    os.remove(test_stacks["dev_stack"] / "test-chart" / "tag.yaml")

    result = update_tag_yaml(test_stacks["dev_stack"], "test-chart", "dev-1.2.3")

    assert result is None


def test_update_tag_yaml_with_commit_sha(test_stacks, mock_metadata):
    """Tests updating tag.yaml with commit SHA."""
    print("\n" + "="*80)
    print("Running test: Update tag.yaml with commit SHA")
    print("="*80)
    print("\nConfiguration:")
    print("  - Stack: dev-keboola-gcp-us-central1")
    print("  - Chart: test-chart")
    print("  - New tag: dev-1.2.3")
    print("  - Commit SHA enabled: True")
    print(f"  - Mock SHA: {mock_metadata['source']['sha']}")

    with patch('helm_image_updater.tag_updater.get_trigger_metadata', return_value=mock_metadata):
        print("\nExecuting update...")
        result = update_tag_yaml(
            test_stacks["dev_stack"],
            "test-chart",
            "dev-1.2.3",
            commit_sha=True
        )

        assert result is True
        print("\nVerifying changes...")

        # Verify the changes
        with open(test_stacks["dev_stack"] / "test-chart" / "tag.yaml") as f:
            data = f.read()
            print("\nResulting tag.yaml content:")
            print(data)
            assert "dev-1.2.3" in data, "New tag should be present in tag.yaml"
            assert "abcdef1" in data, "Short SHA should be present in tag.yaml"

        print("\nTest completed successfully!")


def test_update_tag_yaml_with_commit_sha_disabled(test_stacks, mock_metadata):
    """Tests that commit SHA is not added when disabled."""
    print("\n" + "="*80)
    print("Running test: Update tag.yaml with commit SHA disabled")
    print("="*80)
    print("\nConfiguration:")
    print("  - Stack: dev-keboola-gcp-us-central1")
    print("  - Chart: test-chart")
    print("  - New tag: dev-1.2.3")
    print("  - Commit SHA enabled: False")
    print(f"  - Mock SHA: {mock_metadata['source']['sha']}")

    with patch('helm_image_updater.tag_updater.get_trigger_metadata', return_value=mock_metadata):
        print("\nExecuting update...")
        result = update_tag_yaml(
            test_stacks["dev_stack"],
            "test-chart",
            "dev-1.2.3",
            commit_sha=False
        )

        assert result is True
        print("\nVerifying changes...")

        # Verify the changes
        with open(test_stacks["dev_stack"] / "test-chart" / "tag.yaml") as f:
            data = f.read()
            print("\nResulting tag.yaml content:")
            print(data)
            assert "dev-1.2.3" in data, "New tag should be present in tag.yaml"
            assert "abcdef1" not in data, "SHA should not be present when disabled"

        print("\nTest completed successfully!")


def test_update_tag_yaml_with_commit_sha_no_metadata(test_stacks):
    """Tests handling when metadata is not available."""
    print("\n" + "="*80)
    print("Running test: Update tag.yaml with commit SHA but no metadata")
    print("="*80)
    print("\nConfiguration:")
    print("  - Stack: dev-keboola-gcp-us-central1")
    print("  - Chart: test-chart")
    print("  - New tag: dev-1.2.3")
    print("  - Commit SHA enabled: True")
    print("  - Mock SHA: None (empty metadata)")

    with patch('helm_image_updater.tag_updater.get_trigger_metadata', return_value={}):
        print("\nExecuting update...")
        result = update_tag_yaml(
            test_stacks["dev_stack"],
            "test-chart",
            "dev-1.2.3",
            commit_sha=True
        )

        assert result is True, "Should still update the tag even without metadata"
        print("\nVerifying changes...")

        # Verify the changes
        with open(test_stacks["dev_stack"] / "test-chart" / "tag.yaml") as f:
            data = f.read()
            print("\nResulting tag.yaml content:")
            print(data)
            assert "dev-1.2.3" in data, "New tag should be present in tag.yaml"
            assert "commit_sha" not in data, "SHA field should not be present without metadata"

        print("\nTest completed successfully!")
