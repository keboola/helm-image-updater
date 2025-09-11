"""Legacy test module - migrated to new architecture.

Most functionality from the old tag_updater.py is now covered by:
- test_core.py: Tests for pure business logic functions
- test_cli_functional.py: End-to-end integration tests

This file contains a few remaining integration tests for backward compatibility.
"""

import os
import pytest
from helm_image_updater.io_layer import IOLayer
from helm_image_updater.environment import EnvironmentConfig
from helm_image_updater.plan_builder import prepare_plan
from unittest.mock import Mock


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

    return {
        "base_dir": tmp_path,
        "dev_stack": dev_stack,
        "com_stack": com_stack,
    }


def create_tag_yaml(path, tag):
    """Helper to create tag.yaml files."""
    with open(path, "w", encoding="utf-8") as f:
        f.write(
            f"""image:
  tag: {tag}
"""
        )


def test_plan_with_dry_run(test_stacks):
    """Test plan with dry run."""
    os.chdir(test_stacks["base_dir"])
    
    # Create a mock config
    mock_env = {
        "HELM_CHART": "test-chart",
        "IMAGE_TAG": "dev-1.2.3",
        "GH_TOKEN": "fake-token",
        "AUTOMERGE": "true",
        "DRY_RUN": "true",  # Dry run to avoid actual Git operations
        "MULTI_STAGE": "false",
        "TARGET_PATH": str(test_stacks["base_dir"]),
    }
    
    config = EnvironmentConfig.from_env(mock_env)
    assert config.validate() == []  # Should be valid
    
    # Create mock I/O layer
    mock_repo = Mock()
    mock_github_repo = Mock()
    io_layer = IOLayer(mock_repo, mock_github_repo, dry_run=True)
    
    # Create plan and verify it can be prepared
    plan = prepare_plan(config, io_layer)
    assert plan is not None
    
    # Verify the tag.yaml file exists and has expected content
    tag_file = test_stacks["dev_stack"] / "test-chart" / "tag.yaml"
    assert tag_file.exists()
    
    with open(tag_file) as f:
        content = f.read()
        assert "old-tag" in content


def test_tag_yaml_file_operations(test_stacks):
    """Test basic tag.yaml file operations that are now handled by plan_builder module."""
    from helm_image_updater.plan_builder import calculate_tag_changes
    import yaml
    
    # Read current data
    tag_file = test_stacks["dev_stack"] / "test-chart" / "tag.yaml"
    with open(tag_file) as f:
        current_data = yaml.safe_load(f)
    
    # Calculate changes
    changes = calculate_tag_changes(
        current_data=current_data,
        image_tag="dev-1.2.3"
    )
    
    assert len(changes) == 1
    assert changes[0].path == "image.tag"
    assert changes[0].old_value == "old-tag"
    assert changes[0].new_value == "dev-1.2.3"
    assert changes[0].change_type == "image_tag"


def test_extra_tags_calculation(test_stacks):
    """Test extra tags calculation."""
    from helm_image_updater.plan_builder import calculate_tag_changes
    import yaml
    
    # Read current data and add some nested structure
    tag_file = test_stacks["dev_stack"] / "test-chart" / "tag.yaml"
    with open(tag_file, "w") as f:
        yaml.dump({
            "image": {"tag": "old-tag"},
            "agent": {"image": {"tag": "old-agent-tag"}}
        }, f)
    
    with open(tag_file) as f:
        current_data = yaml.safe_load(f)
    
    # Calculate changes with extra tags
    changes = calculate_tag_changes(
        current_data=current_data,
        image_tag="dev-1.2.3",
        extra_tags=[
            {"path": "agent.image.tag", "value": "dev-2.0.0"}
        ]
    )
    
    assert len(changes) == 2
    # Find the changes by path
    main_change = next(c for c in changes if c.path == "image.tag")
    extra_change = next(c for c in changes if c.path == "agent.image.tag")
    
    assert main_change.old_value == "old-tag"
    assert main_change.new_value == "dev-1.2.3"
    assert extra_change.old_value == "old-agent-tag"
    assert extra_change.new_value == "dev-2.0.0"