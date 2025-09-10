"""Legacy test module - migrated to new architecture.

Most functionality from the old tag_updater.py is now covered by:
- test_core.py: Tests for pure business logic functions
- test_cli_functional.py: End-to-end integration tests

This file contains a few remaining integration tests for backward compatibility.
"""

import os
import pytest
import yaml
from helm_image_updater.io_layer import IOLayer
from helm_image_updater.environment import EnvironmentConfig
from helm_image_updater.plan_builder import prepare_plan, _group_changes_for_prs
from helm_image_updater.models import UpdatePlan, UpdateStrategy
from unittest.mock import Mock


@pytest.fixture
def test_stacks(tmp_path):
    """Creates test stack structure with tag.yaml and shared-values.yaml files."""
    # Create dev stacks (3 clouds)
    dev_gcp = create_stack_with_shared_values(tmp_path / "dev-keboola-gcp-us-central1", "gcp")
    dev_azure = create_stack_with_shared_values(tmp_path / "kbc-testing-azure-east-us-2", "azure") 
    dev_aws = create_stack_with_shared_values(tmp_path / "dev-keboola-aws-eu-west-1", "aws")

    # Create production stacks (3 clouds)
    prod_gcp = create_stack_with_shared_values(tmp_path / "com-keboola-gcp-prod", "gcp")
    prod_azure = create_stack_with_shared_values(tmp_path / "com-keboola-azure-prod", "azure")
    prod_aws = create_stack_with_shared_values(tmp_path / "com-keboola-aws-prod", "aws")

    return {
        "base_dir": tmp_path,
        "dev_gcp": dev_gcp,
        "dev_azure": dev_azure,
        "dev_aws": dev_aws,
        "prod_gcp": prod_gcp,
        "prod_azure": prod_azure,
        "prod_aws": prod_aws,
    }


def create_stack_with_shared_values(stack_path, cloud_provider):
    """Helper to create stack with both tag.yaml and shared-values.yaml."""
    stack_path.mkdir()
    (stack_path / "test-chart").mkdir()
    create_tag_yaml(stack_path / "test-chart" / "tag.yaml", "old-tag")
    
    # Create shared-values.yaml
    shared_values = {"cloudProvider": cloud_provider}
    with open(stack_path / "shared-values.yaml", "w") as f:
        yaml.dump(shared_values, f)
        
    return stack_path


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
    tag_file = test_stacks["dev_gcp"] / "test-chart" / "tag.yaml"
    assert tag_file.exists()
    
    with open(tag_file) as f:
        content = f.read()
        assert "old-tag" in content


def test_tag_yaml_file_operations(test_stacks):
    """Test basic tag.yaml file operations that are now handled by plan_builder module."""
    from helm_image_updater.plan_builder import calculate_tag_changes
    
    # Read current data
    tag_file = test_stacks["dev_gcp"] / "test-chart" / "tag.yaml"
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
    
    # Read current data and add some nested structure
    tag_file = test_stacks["dev_gcp"] / "test-chart" / "tag.yaml"
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


# Multi-cloud grouping tests
def test_multi_cloud_multi_stage_grouping_production_tag(test_stacks):
    """Test multi-cloud multi-stage grouping logic for production tags."""
    from helm_image_updater.plan_builder import _group_changes_for_prs
    
    os.chdir(test_stacks["base_dir"])
    
    # Create mock I/O layer that can read shared-values.yaml
    mock_io_layer = Mock()
    def mock_shared_values(stack):
        cloud_mapping = {
            "dev-keboola-gcp-us-central1": {"cloudProvider": "gcp"},
            "kbc-testing-azure-east-us-2": {"cloudProvider": "azure"},
            "dev-keboola-aws-eu-west-1": {"cloudProvider": "aws"},
            "com-keboola-gcp-prod": {"cloudProvider": "gcp"},
            "com-keboola-azure-prod": {"cloudProvider": "azure"},
            "com-keboola-aws-prod": {"cloudProvider": "aws"},
        }
        return cloud_mapping.get(stack)
    mock_io_layer.read_shared_values_yaml.side_effect = mock_shared_values

    # Create mock environment config
    mock_config = Mock()
    mock_config.automerge = True
    
    # Create mock plan
    mock_plan = Mock()
    mock_plan.multi_stage = True
    mock_plan.strategy = UpdateStrategy.PRODUCTION
    
    # Create mock stack changes for all 6 stacks
    stack_changes = []
    all_stacks = [
        "dev-keboola-gcp-us-central1", "kbc-testing-azure-east-us-2", "dev-keboola-aws-eu-west-1",
        "com-keboola-gcp-prod", "com-keboola-azure-prod", "com-keboola-aws-prod"
    ]
    
    for stack in all_stacks:
        stack_changes.append({
            'stack': stack,
            'file_change': Mock(),
            'changes': []
        })
    
    # Test the grouping
    groups = _group_changes_for_prs(stack_changes, mock_plan, mock_config, mock_io_layer)
    
    # Verify 6 groups were created (3 dev + 3 prod)
    assert len(groups) == 6, f"Expected 6 groups, got {len(groups)}"
    
    # Verify each group has correct properties
    dev_groups = [g for g in groups if g['pr_type'] == 'multi_stage_dev']
    prod_groups = [g for g in groups if g['pr_type'] == 'multi_stage_prod']
    
    assert len(dev_groups) == 3, f"Expected 3 dev groups, got {len(dev_groups)}"
    assert len(prod_groups) == 3, f"Expected 3 prod groups, got {len(prod_groups)}"
    
    # Verify cloud providers are correctly assigned
    dev_clouds = {g['cloud_provider'] for g in dev_groups}
    prod_clouds = {g['cloud_provider'] for g in prod_groups}
    
    expected_clouds = {"aws", "azure", "gcp"}
    assert dev_clouds == expected_clouds, f"Dev clouds {dev_clouds} != expected {expected_clouds}"
    assert prod_clouds == expected_clouds, f"Prod clouds {prod_clouds} != expected {expected_clouds}"
    
    # Verify each group has exactly one stack (one per cloud)
    for group in groups:
        assert len(group['stacks']) == 1, f"Each group should have exactly 1 stack, got {len(group['stacks'])}"
        assert len(group['changes']) == 1, f"Each group should have exactly 1 change, got {len(group['changes'])}"
        assert group['base_branch'] == 'main', f"All groups should target main branch"


def test_multi_cloud_grouping_non_multi_stage(test_stacks):
    """Test that non-multi-stage deployments still work correctly."""
    from helm_image_updater.plan_builder import _group_changes_for_prs
    
    os.chdir(test_stacks["base_dir"])
    
    # Create mock I/O layer
    mock_io_layer = Mock()
    
    # Create mock environment config
    mock_config = Mock()
    mock_config.automerge = True
    
    # Create mock plan (non-multi-stage)
    mock_plan = Mock()
    mock_plan.multi_stage = False  # Non-multi-stage
    mock_plan.strategy = UpdateStrategy.PRODUCTION
    
    # Create mock stack changes for all 6 stacks
    stack_changes = []
    all_stacks = [
        "dev-keboola-gcp-us-central1", "kbc-testing-azure-east-us-2", "dev-keboola-aws-eu-west-1",
        "com-keboola-gcp-prod", "com-keboola-azure-prod", "com-keboola-aws-prod"
    ]
    
    for stack in all_stacks:
        stack_changes.append({
            'stack': stack,
            'file_change': Mock(),
            'changes': []
        })
    
    # Test the grouping
    groups = _group_changes_for_prs(stack_changes, mock_plan, mock_config, mock_io_layer)
    
    # Verify only 1 group was created (normal behavior for non-multi-stage)
    assert len(groups) == 1, f"Non-multi-stage should create 1 group, got {len(groups)}"
    
    # Verify the group contains all stacks
    assert len(groups[0]['stacks']) == 6, f"Group should contain all 6 stacks"
    assert groups[0]['pr_type'] == 'standard', f"PR type should be 'standard'"


def test_multi_cloud_grouping_dev_strategy(test_stacks):
    """Test multi-cloud grouping with dev strategy (should not use multi-stage logic)."""
    from helm_image_updater.plan_builder import _group_changes_for_prs
    
    os.chdir(test_stacks["base_dir"])
    
    # Create mock I/O layer
    mock_io_layer = Mock()
    
    # Create mock environment config
    mock_config = Mock()
    mock_config.automerge = True
    
    # Create mock plan (dev strategy, even with multi_stage=True)
    mock_plan = Mock()
    mock_plan.multi_stage = True
    mock_plan.strategy = UpdateStrategy.DEV  # Dev strategy
    
    # Create mock stack changes for dev stacks only
    stack_changes = []
    dev_stacks = ["dev-keboola-gcp-us-central1", "kbc-testing-azure-east-us-2", "dev-keboola-aws-eu-west-1"]
    
    for stack in dev_stacks:
        stack_changes.append({
            'stack': stack,
            'file_change': Mock(),
            'changes': []
        })
    
    # Test the grouping
    groups = _group_changes_for_prs(stack_changes, mock_plan, mock_config, mock_io_layer)
    
    # Verify only 1 group was created (dev strategy doesn't use multi-cloud logic)
    assert len(groups) == 1, f"Dev strategy should create 1 group regardless of multi-stage, got {len(groups)}"
    
    # Verify the group contains all dev stacks
    assert len(groups[0]['stacks']) == 3, f"Group should contain all 3 dev stacks"
    assert groups[0]['pr_type'] == 'standard', f"PR type should be 'standard'"