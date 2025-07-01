"""Test module for tag update strategies.

Tests the different update scenarios for dev and production tags,
including auto-merge and multi-stage deployment cases.
"""

import os
from unittest.mock import Mock, patch
import pytest
from helm_image_updater.config import UpdateConfig, CANARY_STACKS
from helm_image_updater.tag_updater import (
    handle_dev_tag,
    handle_production_tag,
    handle_canary_tag,
    update_tag_yaml,
    update_stack_by_id,
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

    e2e_stack = tmp_path / "dev-keboola-gcp-us-east1-e2e"
    e2e_stack.mkdir()
    (e2e_stack / "test-chart").mkdir()
    create_tag_yaml(e2e_stack / "test-chart" / "tag.yaml", "old-tag")

    # Create production stacks
    com_stack = tmp_path / "com-keboola-prod"
    com_stack.mkdir()
    (com_stack / "test-chart").mkdir()
    create_tag_yaml(com_stack / "test-chart" / "tag.yaml", "old-tag")

    cloud_stack = tmp_path / "cloud-keboola-prod"
    cloud_stack.mkdir()
    (cloud_stack / "test-chart").mkdir()
    create_tag_yaml(cloud_stack / "test-chart" / "tag.yaml", "old-tag")

    # Create canary stacks
    canary_orion = tmp_path / "dev-keboola-canary-orion"
    canary_orion.mkdir()
    (canary_orion / "test-chart").mkdir()
    create_tag_yaml(canary_orion / "test-chart" / "tag.yaml", "old-tag")

    canary_ursa = tmp_path / "dev-keboola-canary-ursa"
    canary_ursa.mkdir()
    (canary_ursa / "test-chart").mkdir()
    create_tag_yaml(canary_ursa / "test-chart" / "tag.yaml", "old-tag")

    return {
        "base_dir": tmp_path,
        "dev_stack": dev_stack,
        "com_stack": com_stack,
        "cloud_stack": cloud_stack,
        "canary_orion": canary_orion,
        "canary_ursa": canary_ursa,
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
            "name": "Development tag with auto-merge enabled should create a single PR",
            "image_tag": "dev-1.2.3",
            "automerge": True,
            "multi_stage": False,
            "expected_pr_count": 1,
            "expected_stacks": ["dev-keboola-gcp-us-central1"],
            "expected_base": "main",
        },
        {
            "name": "Development tag without auto-merge should create a single PR",
            "image_tag": "dev-1.2.3",
            "automerge": False,
            "multi_stage": False,
            "expected_pr_count": 1,
            "expected_stacks": ["dev-keboola-gcp-us-central1"],
            "expected_base": "main",
        },
        {
            "name": "Production tag with auto-merge should create a single PR for all stacks",
            "image_tag": "production-1.2.3",
            "automerge": True,
            "multi_stage": False,
            "expected_pr_count": 1,
            "expected_stacks": [
                "dev-keboola-gcp-us-central1",
                "com-keboola-prod",
                "cloud-keboola-prod",
            ],
            "expected_base": "main",
        },
        {
            "name": "Production tag without auto-merge should create separate PRs for each stack",
            "image_tag": "production-1.2.3",
            "automerge": False,
            "multi_stage": False,
            "expected_pr_count": 3,  # One per stack (2 prod + 1 dev)
            "expected_stacks": [
                "dev-keboola-gcp-us-central1",
                "com-keboola-prod",
                "cloud-keboola-prod",
            ],
            "expected_base": "main",
        },
        {
            "name": "Production tag with multi-stage deployment should create separate PRs for dev and prod",
            "image_tag": "production-1.2.3",
            "automerge": True,  # Should be ignored for prod stacks
            "multi_stage": True,
            "expected_pr_count": 2,  # One for dev, one for prod
            "expected_stacks": [
                "dev-keboola-gcp-us-central1",
                "com-keboola-prod",
                "cloud-keboola-prod",
            ],
            "expected_base": "main",
        },
        {
            "name": "Canary Orion tag should create a PR targeting the canary-orion branch",
            "image_tag": "canary-orion-1.2.3",
            "automerge": False,  # Should be ignored for canary (always auto-merges)
            "multi_stage": False,
            "expected_pr_count": 1,
            "expected_stacks": ["dev-keboola-canary-orion"],
            "expected_base": "canary-orion",
        },
        {
            "name": "Canary Ursa tag should create a PR targeting the canary-ursa branch",
            "image_tag": "canary-ursa-1.2.3",
            "automerge": False,  # Should be ignored for canary (always auto-merges)
            "multi_stage": False,
            "expected_pr_count": 1,
            "expected_stacks": ["dev-keboola-canary-ursa"],
            "expected_base": "canary-ursa",
        },
    ],
)
def test_tag_update_strategy(
    test_stacks, mock_repo, mock_github_repo, test_case, monkeypatch, github_context
):
    """Test different tag update strategies.

    This test verifies that:
    1. The correct number of PRs are created based on the update strategy
    2. The right stacks are updated
    3. PRs target the correct base branch
    4. Auto-merge settings are respected
    5. Multi-stage deployment behavior is correct
    """
    print("\n" + "=" * 80)
    print(f"Running test: {test_case['name']}")
    print("=" * 80)

    print("\nTest configuration:")
    print(f"  - Image tag: {test_case['image_tag']}")
    print(f"  - Auto-merge enabled: {test_case['automerge']}")
    print(f"  - Multi-stage deployment: {test_case['multi_stage']}")
    print(f"  - Expected PR count: {test_case['expected_pr_count']}")
    print(f"  - Expected stacks to update: {', '.join(test_case['expected_stacks'])}")
    print(f"  - Expected base branch: {test_case['expected_base']}")

    os.chdir(test_stacks["base_dir"])

    def mock_listdir(path):
        """Mock os.listdir to return our test stacks."""
        return [
            "dev-keboola-gcp-us-central1",
            "com-keboola-prod",
            "cloud-keboola-prod",
            "dev-keboola-canary-orion",
            "dev-keboola-canary-ursa",
            ".git",
        ]

    monkeypatch.setattr(os, "listdir", mock_listdir)

    config = UpdateConfig(
        repo=mock_repo,
        github_repo=mock_github_repo,
        helm_chart="test-chart",
        image_tag=test_case["image_tag"],
        automerge=test_case["automerge"],
        multi_stage=test_case["multi_stage"],
    )

    print("\nInitialized test configuration")

    # Track created PRs and their properties
    created_prs = []

    def mock_create_pr(config, branch_name, pr_title, base="main"):
        """Mock PR creation to track PR details."""
        created_prs.append({"branch": branch_name, "title": pr_title, "base": base})
        print("\nCreated PR:")
        print(f"  - Branch: {branch_name}")
        print(f"  - Title: {pr_title}")
        print(f"  - Base: {base}")

    monkeypatch.setattr("helm_image_updater.tag_updater.create_pr", mock_create_pr)

    print("\nExecuting update...")
    # Call appropriate handler based on tag type
    if test_case["image_tag"].startswith("dev-"):
        changes, missing = handle_dev_tag(config)
    elif test_case["image_tag"].startswith("production-"):
        changes, missing = handle_production_tag(config)
    else:
        # Must be a canary tag
        changes, missing = handle_canary_tag(config)

    print("\nVerifying results:")
    # Verify number of PRs created
    print(
        f"  - Checking PR count: expected {test_case['expected_pr_count']}, got {len(created_prs)}"
    )
    assert len(created_prs) == test_case["expected_pr_count"]

    # Verify correct stacks were updated
    updated_stacks = [change["stack"] for change in changes]
    print(f"  - Updated stacks: {', '.join(updated_stacks)}")
    print(f"  - Expected stacks: {', '.join(test_case['expected_stacks'])}")
    assert sorted(updated_stacks) == sorted(test_case["expected_stacks"])

    # Verify base branch
    for pr in created_prs:
        print(
            f"  - Checking base branch for PR {pr['branch']}: expected {test_case['expected_base']}, got {pr['base']}"
        )
        assert pr["base"] == test_case["expected_base"]

    # For canary updates, verify auto-merge is always True
    if any(
        test_case["image_tag"].startswith(prefix) for prefix in CANARY_STACKS.keys()
    ):
        print("  - Verifying canary updates are always auto-merged")
        assert all(change["automerge"] for change in changes)

    print("\nTest completed successfully!")


def test_update_tag_yaml_with_extra_tags(test_stacks):
    """Tests updating tag.yaml with extra tags."""
    print("\n" + "=" * 80)
    print("Running test: Update tag.yaml with extra tags")
    print("=" * 80)

    extra_tags = [
        {"path": "agent.image.tag", "value": "dev-1.0.0"},
        {"path": "sidecar.tag", "value": "production-2.0.0"},
    ]

    print("\nTest configuration:")
    print("  - Stack: dev-keboola-gcp-us-central1")
    print("  - Chart: test-chart")
    print("  - Main tag: dev-1.2.3")
    print("  - Extra tags:")
    for tag in extra_tags:
        print(f"    - {tag['path']}: {tag['value']}")

    print("\nExecuting update...")
    result = update_tag_yaml(
        test_stacks["dev_stack"], "test-chart", "dev-1.2.3", extra_tags=extra_tags
    )

    print("\nVerifying changes...")
    assert result is True, "Update should be successful"

    # Verify the changes
    with open(test_stacks["dev_stack"] / "test-chart" / "tag.yaml") as f:
        data = f.read()
        print("\nResulting tag.yaml content:")
        print(data)
        assert "dev-1.2.3" in data, "Main tag should be present"
        assert "dev-1.0.0" in data, "First extra tag should be present"
        assert "production-2.0.0" in data, "Second extra tag should be present"

    print("\nTest completed successfully!")


def test_missing_tag_yaml(test_stacks):
    """Tests handling of missing tag.yaml files."""
    print("\n" + "=" * 80)
    print("Running test: Handle missing tag.yaml file")
    print("=" * 80)

    print("\nTest configuration:")
    print("  - Stack: dev-keboola-gcp-us-central1")
    print("  - Chart: test-chart")
    print("  - Tag: dev-1.2.3")

    # Remove tag.yaml
    tag_yaml_path = test_stacks["dev_stack"] / "test-chart" / "tag.yaml"
    print(f"\nRemoving file: {tag_yaml_path}")
    os.remove(tag_yaml_path)

    print("\nExecuting update...")
    result = update_tag_yaml(test_stacks["dev_stack"], "test-chart", "dev-1.2.3")

    print("\nVerifying result...")
    print("  - Expected: None (file missing)")
    print(f"  - Actual: {result}")
    assert result is None, "Should return None for missing tag.yaml"

    print("\nTest completed successfully!")


def test_update_tag_yaml_with_commit_sha(test_stacks, mock_metadata):
    """Tests updating tag.yaml with commit SHA."""
    print("\n" + "=" * 80)
    print("Running test: Update tag.yaml with commit SHA")
    print("=" * 80)
    print("\nConfiguration:")
    print("  - Stack: dev-keboola-gcp-us-central1")
    print("  - Chart: test-chart")
    print("  - New tag: dev-1.2.3")
    print("  - Commit SHA enabled: True")
    print(f"  - Mock SHA: {mock_metadata['source']['sha']}")

    with patch(
        "helm_image_updater.tag_updater.get_trigger_metadata",
        return_value=mock_metadata,
    ):
        print("\nExecuting update...")
        result = update_tag_yaml(
            test_stacks["dev_stack"], "test-chart", "dev-1.2.3", commit_sha=True
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
    print("\n" + "=" * 80)
    print("Running test: Update tag.yaml with commit SHA disabled")
    print("=" * 80)
    print("\nConfiguration:")
    print("  - Stack: dev-keboola-gcp-us-central1")
    print("  - Chart: test-chart")
    print("  - New tag: dev-1.2.3")
    print("  - Commit SHA enabled: False")
    print(f"  - Mock SHA: {mock_metadata['source']['sha']}")

    with patch(
        "helm_image_updater.tag_updater.get_trigger_metadata",
        return_value=mock_metadata,
    ):
        print("\nExecuting update...")
        result = update_tag_yaml(
            test_stacks["dev_stack"], "test-chart", "dev-1.2.3", commit_sha=False
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
    print("\n" + "=" * 80)
    print("Running test: Update tag.yaml with commit SHA but no metadata")
    print("=" * 80)
    print("\nConfiguration:")
    print("  - Stack: dev-keboola-gcp-us-central1")
    print("  - Chart: test-chart")
    print("  - New tag: dev-1.2.3")
    print("  - Commit SHA enabled: True")
    print("  - Mock SHA: None (empty metadata)")

    with patch("helm_image_updater.tag_updater.get_trigger_metadata", return_value={}):
        print("\nExecuting update...")
        result = update_tag_yaml(
            test_stacks["dev_stack"], "test-chart", "dev-1.2.3", commit_sha=True
        )

        assert result is True, "Should still update the tag even without metadata"
        print("\nVerifying changes...")

        # Verify the changes
        with open(test_stacks["dev_stack"] / "test-chart" / "tag.yaml") as f:
            data = f.read()
            print("\nResulting tag.yaml content:")
            print(data)
            assert "dev-1.2.3" in data, "New tag should be present in tag.yaml"
            assert "commit_sha" not in data, (
                "SHA field should not be present without metadata"
            )

        print("\nTest completed successfully!")


def test_canary_tag_with_extra_tags(
    test_stacks, mock_repo, mock_github_repo, monkeypatch
):
    """Test canary tag update with extra tags."""
    print("\n" + "=" * 80)
    print("Running test: Canary tag update with extra tags")
    print("=" * 80)

    extra_tags = [
        {"path": "agent.image.tag", "value": "dev-2.0.0"},
        {"path": "messenger.image.tag", "value": "production-3.0.0"},
    ]

    print("\nTest configuration:")
    print("  - Stack: dev-keboola-canary-orion")
    print("  - Chart: test-chart")
    print("  - Canary tag: canary-orion-1.2.3")
    print("  - Auto-merge: False (should be ignored for canary)")
    print("  - Extra tags:")
    for tag in extra_tags:
        print(f"    - {tag['path']}: {tag['value']}")

    os.chdir(test_stacks["base_dir"])

    config = UpdateConfig(
        repo=mock_repo,
        github_repo=mock_github_repo,
        helm_chart="test-chart",
        image_tag="canary-orion-1.2.3",
        automerge=False,  # Should be ignored for canary
        extra_tags=extra_tags,
    )

    created_prs = []

    def mock_create_pr(config, branch_name, pr_title, base="main"):
        """Mock PR creation to track PR details."""
        created_prs.append({"branch": branch_name, "title": pr_title, "base": base})
        print("\nCreated PR:")
        print(f"  - Branch: {branch_name}")
        print(f"  - Title: {pr_title}")
        print(f"  - Base: {base}")

    monkeypatch.setattr("helm_image_updater.tag_updater.create_pr", mock_create_pr)

    print("\nExecuting update...")
    changes, missing = handle_canary_tag(config)

    print("\nVerifying results:")
    # Verify single PR created
    print(f"  - Checking PR count: expected 1, got {len(created_prs)}")
    assert len(created_prs) == 1

    # Verify correct base branch
    print(
        f"  - Checking base branch: expected canary-orion, got {created_prs[0]['base']}"
    )
    assert created_prs[0]["base"] == "canary-orion"

    # Verify correct stack updated
    print("  - Checking updated stacks...")
    assert len(changes) == 1, "Should update exactly one stack"
    print(f"    - Updated stack: {changes[0]['stack']}")
    assert changes[0]["stack"] == "dev-keboola-canary-orion"

    # Verify auto-merge is True
    print("  - Verifying auto-merge setting...")
    assert changes[0]["automerge"] is True, "Canary updates should always auto-merge"

    print("\nTest completed successfully!")


def test_update_stack_by_id(test_stacks, mock_repo, mock_github_repo, monkeypatch):
    """Test updating a stack by ID."""
    print("\n" + "=" * 80)
    print("Running test: Update stack by ID")
    print("=" * 80)

    # Mock the create_pr function to avoid actual PR creation
    pr_created = []

    def mock_create_pr(config, branch_name, pr_title, base="main"):
        print(f"Would create PR: {pr_title} (branch: {branch_name}, base: {base})")
        pr_created.append({"branch": branch_name, "title": pr_title, "base": base})

    # Apply the mocks
    monkeypatch.setattr("helm_image_updater.tag_updater.create_pr", mock_create_pr)

    # Create test config
    from helm_image_updater.config import UpdateConfig

    config = UpdateConfig(
        repo=mock_repo,
        github_repo=mock_github_repo,
        helm_chart="test-chart",
        image_tag="dev-1.2.3",
        automerge=True,
    )

    # Test updating a dev stack with a dev tag
    print("\nUpdating dev stack with dev tag:")
    updated_stacks, failed_stacks = update_stack_by_id(
        config, "dev-keboola-gcp-us-central1"
    )
    assert len(updated_stacks) == 1, "Should return one updated stack"
    assert len(failed_stacks) == 0, "Should not have any failed stacks"
    assert updated_stacks[0]["stack"] == "dev-keboola-gcp-us-central1"
    assert len(pr_created) == 1, "Should create a PR"

    # Test updating a dev stack with a custom tag (not starting with dev- or production-)
    print("\nUpdating dev stack with custom tag:")
    config.image_tag = "custom-1.2.3"
    pr_created.clear()
    updated_stacks, failed_stacks = update_stack_by_id(
        config, "dev-keboola-gcp-us-central1"
    )
    assert len(updated_stacks) == 1, "Should return one updated stack"
    assert len(failed_stacks) == 0, "Should not have any failed stacks"
    assert updated_stacks[0]["stack"] == "dev-keboola-gcp-us-central1"
    assert len(pr_created) == 1, "Should create a PR"

    # Test updating a production stack with a production tag
    print("\nUpdating production stack with production tag:")
    config.image_tag = "production-1.2.3"
    pr_created.clear()
    updated_stacks, failed_stacks = update_stack_by_id(config, "com-keboola-prod")
    assert len(updated_stacks) == 1, "Should return one updated stack"
    assert len(failed_stacks) == 0, "Should not have any failed stacks"
    assert updated_stacks[0]["stack"] == "com-keboola-prod"
    assert len(pr_created) == 1, "Should create a PR"

    # Test incompatible tag and stack (non-production tag with production stack)
    print("\nTesting incompatible tag and stack:")
    config.image_tag = "custom-1.2.3"
    pr_created.clear()
    updated_stacks, failed_stacks = update_stack_by_id(config, "com-keboola-prod")
    assert len(updated_stacks) == 0, (
        "Should not have any updated stacks for incompatible tag and stack"
    )
    assert len(failed_stacks) == 0, (
        "Should not have any failed stacks for incompatible tag and stack"
    )
    assert len(pr_created) == 0, "Should not create a PR"

    # Test updating a E2E stack with a build tag
    print("\nUpdating E2E stack with build tag:")
    config.image_tag = "martin-dev-1.2.3"
    pr_created.clear()
    updated_stacks, failed_stacks = update_stack_by_id(
        config, "dev-keboola-gcp-us-east1-e2e"
    )
    assert len(updated_stacks) == 1, "Should return one updated stack"
    assert len(failed_stacks) == 0, "Should not have any failed stacks"
    assert updated_stacks[0]["stack"] == "dev-keboola-gcp-us-east1-e2e"
    assert len(pr_created) == 1, "Should create a PR"
