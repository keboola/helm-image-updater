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
from helm_image_updater.plan_builder import prepare_plan, _group_changes_for_prs, _check_and_remove_override
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
        "GH_APPROVE_TOKEN": "fake-approve-token",
    }
    
    config = EnvironmentConfig.from_env(mock_env)
    assert config.validate() == []  # Should be valid
    
    # Create mock I/O layer
    mock_repo = Mock()
    mock_github_repo = Mock()
    mock_approve_github_repo = Mock()
    io_layer = IOLayer(mock_repo, mock_github_repo, dry_run=True, approve_github_repo=mock_approve_github_repo)
    
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


# Override removal tests

class TestCheckAndRemoveOverride:
    """Tests for _check_and_remove_override function."""

    def test_removes_override_with_branch(self):
        """Override with a feature branch name is removed."""
        mock_io = Mock()
        mock_io.read_file.return_value = yaml.dump({
            "argocdApplication": {"appManifestsRevision": "feature-branch-123"}
        })

        result = _check_and_remove_override("dev-stack", "my-chart", mock_io)

        assert result is not None
        assert result.file_path == "dev-stack/my-chart/values.yaml"
        assert "feature-branch-123" in result.change_description

        assert result.new_content == ""

    def test_removes_override_leaves_empty_file_when_only_override_present(self):
        """When values.yaml contains only the argocdApplication block, result is an empty file."""
        mock_io = Mock()
        mock_io.read_file.return_value = yaml.dump({
            "argocdApplication": {"appManifestsRevision": "feature-branch-123"}
        })

        result = _check_and_remove_override("dev-stack", "my-chart", mock_io)

        assert result is not None
        assert result.new_content == ""

    def test_no_values_yaml(self):
        """Returns None when values.yaml doesn't exist."""
        mock_io = Mock()
        mock_io.read_file.return_value = None

        result = _check_and_remove_override("dev-stack", "my-chart", mock_io)
        assert result is None

    def test_no_override_key(self):
        """Returns None when argocdApplication is not present."""
        mock_io = Mock()
        mock_io.read_file.return_value = yaml.dump({
            "image": {"repository": "keboola/my-service"}
        })

        result = _check_and_remove_override("dev-stack", "my-chart", mock_io)
        assert result is None

    def test_override_set_to_main(self):
        """Returns None when override is set to 'main' (already correct)."""
        mock_io = Mock()
        mock_io.read_file.return_value = yaml.dump({
            "argocdApplication": {"appManifestsRevision": "main"}
        })

        result = _check_and_remove_override("dev-stack", "my-chart", mock_io)
        assert result is None

    def test_preserves_other_argocd_fields(self):
        """Only removes appManifestsRevision, keeps other argocdApplication fields."""
        mock_io = Mock()
        mock_io.read_file.return_value = yaml.dump({
            "argocdApplication": {
                "appManifestsRevision": "feature-branch",
                "syncPolicy": "automated",
            }
        })

        result = _check_and_remove_override("dev-stack", "my-chart", mock_io)

        assert result is not None
        new_data = yaml.safe_load(result.new_content)
        assert "argocdApplication" in new_data
        assert "appManifestsRevision" not in new_data["argocdApplication"]
        assert new_data["argocdApplication"]["syncPolicy"] == "automated"

    def test_preserves_other_top_level_keys(self):
        """Other top-level keys in values.yaml are preserved."""
        mock_io = Mock()
        mock_io.read_file.return_value = yaml.dump({
            "image": {"repository": "keboola/my-service"},
            "argocdApplication": {"appManifestsRevision": "feature-branch"},
            "resources": {"limits": {"cpu": "100m"}},
        })

        result = _check_and_remove_override("dev-stack", "my-chart", mock_io)

        assert result is not None
        new_data = yaml.safe_load(result.new_content)
        assert new_data["image"]["repository"] == "keboola/my-service"
        assert new_data["resources"]["limits"]["cpu"] == "100m"
        assert "argocdApplication" not in new_data

    def test_empty_argocd_block_no_revision(self):
        """Returns None when argocdApplication exists but has no appManifestsRevision."""
        mock_io = Mock()
        mock_io.read_file.return_value = yaml.dump({
            "argocdApplication": {"syncPolicy": "automated"}
        })

        result = _check_and_remove_override("dev-stack", "my-chart", mock_io)
        assert result is None

    def test_invalid_yaml(self):
        """Returns None when values.yaml contains invalid YAML."""
        mock_io = Mock()
        mock_io.read_file.return_value = "{{invalid yaml: ["

        result = _check_and_remove_override("dev-stack", "my-chart", mock_io)
        assert result is None

    def test_values_yaml_is_just_a_string(self):
        """Returns None when values.yaml parses to a non-dict (e.g. a plain string)."""
        mock_io = Mock()
        mock_io.read_file.return_value = "just a string"

        result = _check_and_remove_override("dev-stack", "my-chart", mock_io)
        assert result is None


class TestOverrideIntegration:
    """Integration tests for override removal in the full plan flow."""

    def test_plan_includes_override_removal(self, tmp_path, monkeypatch):
        """prepare_plan includes override FileChange when values.yaml has an override."""
        # Set up a dev stack with tag.yaml and values.yaml with override
        stack_name = "dev-keboola-gcp-us-central1"
        stack_dir = tmp_path / stack_name
        chart_dir = stack_dir / "test-chart"
        chart_dir.mkdir(parents=True)

        # Create tag.yaml
        create_tag_yaml(chart_dir / "tag.yaml", "old-tag")

        # Create values.yaml with override
        with open(chart_dir / "values.yaml", "w") as f:
            yaml.dump({"argocdApplication": {"appManifestsRevision": "feature-branch"}}, f)

        # Create shared-values.yaml
        with open(stack_dir / "shared-values.yaml", "w") as f:
            yaml.dump({"cloudProvider": "gcp"}, f)

        monkeypatch.chdir(tmp_path)

        mock_env = {
            "HELM_CHART": "test-chart",
            "IMAGE_TAG": "dev-new-tag",
            "GH_TOKEN": "fake-token",
            "AUTOMERGE": "true",
            "DRY_RUN": "true",
            "MULTI_STAGE": "false",
            "TARGET_PATH": str(tmp_path),
        }

        config = EnvironmentConfig.from_env(mock_env)
        mock_repo = Mock()
        mock_github_repo = Mock()
        io_layer = IOLayer(mock_repo, mock_github_repo, dry_run=True, approve_github_repo=Mock())

        plan = prepare_plan(config, io_layer)

        # Should have 2 file changes: tag.yaml + values.yaml
        assert len(plan.file_changes) == 2
        file_paths = [fc.file_path for fc in plan.file_changes]
        assert f"{stack_name}/test-chart/tag.yaml" in file_paths
        assert f"{stack_name}/test-chart/values.yaml" in file_paths

        # PR should include both files
        assert len(plan.pr_plans) == 1
        assert f"{stack_name}/test-chart/values.yaml" in plan.pr_plans[0].files_to_commit

    def test_plan_without_override_has_only_tag_change(self, tmp_path, monkeypatch):
        """prepare_plan only has tag.yaml change when no override exists."""
        stack_name = "dev-keboola-gcp-us-central1"
        stack_dir = tmp_path / stack_name
        chart_dir = stack_dir / "test-chart"
        chart_dir.mkdir(parents=True)

        create_tag_yaml(chart_dir / "tag.yaml", "old-tag")

        # Create shared-values.yaml
        with open(stack_dir / "shared-values.yaml", "w") as f:
            yaml.dump({"cloudProvider": "gcp"}, f)

        monkeypatch.chdir(tmp_path)

        mock_env = {
            "HELM_CHART": "test-chart",
            "IMAGE_TAG": "dev-new-tag",
            "GH_TOKEN": "fake-token",
            "AUTOMERGE": "true",
            "DRY_RUN": "true",
            "MULTI_STAGE": "false",
            "TARGET_PATH": str(tmp_path),
        }

        config = EnvironmentConfig.from_env(mock_env)
        mock_repo = Mock()
        mock_github_repo = Mock()
        io_layer = IOLayer(mock_repo, mock_github_repo, dry_run=True, approve_github_repo=Mock())

        plan = prepare_plan(config, io_layer)

        # Should have only 1 file change: tag.yaml
        assert len(plan.file_changes) == 1
        assert plan.file_changes[0].file_path == f"{stack_name}/test-chart/tag.yaml"