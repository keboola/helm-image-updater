"""Unit tests for core business logic (pure functions).

These tests demonstrate the simplicity of testing pure functions
without any mocks or complex setup. Each test is fast and deterministic.
"""

import pytest
from unittest.mock import Mock
from helm_image_updater.tag_classification import detect_tag_type, TagType
from helm_image_updater.message_generation import generate_pr_title_prefix
from helm_image_updater.models import UpdateStrategy
from helm_image_updater.stack_classification import classify_stack, filter_stacks_by_type
from helm_image_updater.cloud_detection import get_stack_cloud_provider, classify_stacks_by_cloud, StackCloudInfo


class TestTagTypeDetection:
    """Test tag type detection logic."""
    
    def test_dev_tag(self):
        """Test detection of development tags."""
        assert detect_tag_type("dev-123abc") == TagType.DEV
        assert detect_tag_type("dev-feature-branch") == TagType.DEV
        
    def test_production_tag(self):
        """Test detection of production tags."""
        assert detect_tag_type("production-123abc") == TagType.PRODUCTION
        assert detect_tag_type("production-release") == TagType.PRODUCTION
        
    def test_semver_tag(self):
        """Test detection of semver tags."""
        assert detect_tag_type("1.2.3") == TagType.SEMVER
        assert detect_tag_type("v1.2.3") == TagType.SEMVER
        assert detect_tag_type("0.0.1") == TagType.SEMVER
        
    def test_canary_tag(self):
        """Test detection of canary tags."""
        assert detect_tag_type("canary-orion-123") == TagType.CANARY
        
    def test_invalid_tag(self):
        """Test detection of invalid tags."""
        assert detect_tag_type("invalid-tag") == TagType.INVALID
        assert detect_tag_type("") == TagType.INVALID
        assert detect_tag_type("  ") == TagType.INVALID





class TestStackClassification:
    """Test stack classification logic."""
    
    def test_dev_stack_gcp(self):
        """Test classification of GCP dev stack."""
        result = classify_stack("dev-keboola-gcp-us-central1")
        assert result.is_dev
        assert not result.is_production
        assert not result.is_canary
        
    def test_dev_stack_azure(self):
        """Test classification of Azure dev stack."""
        result = classify_stack("kbc-testing-azure-east-us-2")
        assert result.is_dev
        assert not result.is_production
        assert not result.is_canary
        
    def test_dev_stack_aws(self):
        """Test classification of AWS dev stack."""
        result = classify_stack("dev-keboola-aws-eu-west-1")
        assert result.is_dev
        assert not result.is_production
        assert not result.is_canary
        
    def test_production_stack(self):
        """Test classification of production stacks."""
        result = classify_stack("com-keboola-prod")
        assert not result.is_dev
        assert result.is_production
        assert not result.is_canary
        
    def test_canary_stack(self):
        """Test classification of canary stacks."""
        result = classify_stack("dev-keboola-canary-orion")
        assert not result.is_dev
        assert not result.is_production
        assert result.is_canary
        
    def test_excluded_stack(self):
        """Test classification of excluded stacks."""
        result = classify_stack("dev-keboola-gcp-us-east1-e2e")
        assert result.is_excluded
        assert not result.is_dev
        assert not result.is_production


class TestStackFiltering:
    """Test stack filtering logic."""
    
    def test_filter_dev_stacks(self):
        """Test filtering for dev stacks."""
        all_stacks = [
            "dev-keboola-gcp-us-central1",  # GCP dev
            "kbc-testing-azure-east-us-2",  # Azure dev  
            "dev-keboola-aws-eu-west-1",    # AWS dev
            "com-keboola-prod",
            "dev-keboola-canary-orion",
            "dev-keboola-gcp-us-east1-e2e",  # excluded
        ]
        
        result = filter_stacks_by_type(all_stacks, "dev")
        expected = [
            "dev-keboola-gcp-us-central1", 
            "kbc-testing-azure-east-us-2", 
            "dev-keboola-aws-eu-west-1"
        ]
        assert sorted(result) == sorted(expected)
        
    def test_filter_production_stacks(self):
        """Test filtering for production stacks."""
        all_stacks = [
            "dev-keboola-gcp-us-central1",
            "com-keboola-prod",
            "cloud-keboola-prod",
        ]
        
        result = filter_stacks_by_type(all_stacks, "production")
        assert "com-keboola-prod" in result
        assert "cloud-keboola-prod" in result
        assert "dev-keboola-gcp-us-central1" not in result


class TestPRTitleGeneration:
    """Test PR title prefix generation."""
    
    def test_canary_pr_title(self):
        """Test canary PR title generation."""
        prefix = generate_pr_title_prefix(
            strategy=UpdateStrategy.CANARY,
            is_multi_stage=False,
            user_requested_automerge=True,
            target_stacks=["dev-keboola-canary-orion"]
        )
        assert prefix == "[canary sync]"
        
    def test_dev_pr_title(self):
        """Test dev PR title generation."""
        prefix = generate_pr_title_prefix(
            strategy=UpdateStrategy.DEV,
            is_multi_stage=False,
            user_requested_automerge=True,
            target_stacks=["dev-keboola-gcp-us-central1"]
        )
        assert prefix == "[test sync]"
        
    def test_multi_stage_dev_pr_title(self):
        """Test multi-stage dev PR title generation."""
        # With automerge
        prefix = generate_pr_title_prefix(
            strategy=UpdateStrategy.DEV,
            is_multi_stage=True,
            user_requested_automerge=True,
            target_stacks=["dev-keboola-gcp-us-central1"]
        )
        assert prefix == "[multi-stage] [test sync]"
        
        # Without automerge
        prefix = generate_pr_title_prefix(
            strategy=UpdateStrategy.DEV,
            is_multi_stage=True,
            user_requested_automerge=False,
            target_stacks=["dev-keboola-gcp-us-central1"]
        )
        assert prefix == "[multi-stage] [test sync manual]"
        
    def test_multi_cloud_dev_pr_title_with_cloud_provider(self):
        """Test multi-cloud dev PR title generation with cloud provider."""
        # With automerge and cloud provider
        prefix = generate_pr_title_prefix(
            strategy=UpdateStrategy.DEV,
            is_multi_stage=True,
            user_requested_automerge=True,
            target_stacks=["dev-keboola-gcp-us-central1"],
            cloud_provider="gcp"
        )
        assert prefix == "[multi-stage] [test sync gcp]"
        
        # Without automerge and cloud provider
        prefix = generate_pr_title_prefix(
            strategy=UpdateStrategy.DEV,
            is_multi_stage=True,
            user_requested_automerge=False,
            target_stacks=["kbc-testing-azure-east-us-2"],
            cloud_provider="azure"
        )
        assert prefix == "[multi-stage] [test sync azure manual]"
        
    def test_multi_cloud_prod_pr_title_with_cloud_provider(self):
        """Test multi-cloud production PR title generation with cloud provider."""
        prefix = generate_pr_title_prefix(
            strategy=UpdateStrategy.PRODUCTION,
            is_multi_stage=True,
            user_requested_automerge=True,
            target_stacks=["com-keboola-aws-prod"],
            cloud_provider="aws"
        )
        assert prefix == "[multi-stage] [prod sync aws]"


class TestCloudDetection:
    """Test cloud provider detection logic."""
    
    def test_get_stack_cloud_provider_dev_stack(self):
        """Test cloud provider detection for dev stacks."""
        # Mock IO layer
        mock_io_layer = Mock()
        mock_io_layer.read_shared_values_yaml.return_value = {"cloudProvider": "gcp"}
        
        result = get_stack_cloud_provider("dev-keboola-gcp-us-central1", mock_io_layer)
        assert result == "gcp"
        
        mock_io_layer.read_shared_values_yaml.assert_called_once_with("dev-keboola-gcp-us-central1")
        
    def test_get_stack_cloud_provider_prod_stack(self):
        """Test cloud provider detection for production stacks."""
        # Mock IO layer  
        mock_io_layer = Mock()
        mock_io_layer.read_shared_values_yaml.return_value = {"cloudProvider": "azure"}
        
        result = get_stack_cloud_provider("com-keboola-azure-prod", mock_io_layer)
        assert result == "azure"
        
    def test_get_stack_cloud_provider_missing_yaml(self):
        """Test error handling for missing shared-values.yaml."""
        mock_io_layer = Mock()
        mock_io_layer.read_shared_values_yaml.return_value = None
        
        with pytest.raises(ValueError, match="Missing cloudProvider in test-stack/shared-values.yaml"):
            get_stack_cloud_provider("test-stack", mock_io_layer)
            
    def test_get_stack_cloud_provider_missing_field(self):
        """Test error handling for missing cloudProvider field."""
        mock_io_layer = Mock()
        mock_io_layer.read_shared_values_yaml.return_value = {"someOtherField": "value"}
        
        with pytest.raises(ValueError, match="Missing cloudProvider in test-stack/shared-values.yaml"):
            get_stack_cloud_provider("test-stack", mock_io_layer)
            
    def test_get_stack_cloud_provider_invalid_provider(self):
        """Test error handling for invalid cloud provider."""
        mock_io_layer = Mock()
        mock_io_layer.read_shared_values_yaml.return_value = {"cloudProvider": "invalid"}
        
        with pytest.raises(ValueError, match="Unsupported cloudProvider 'invalid' in test-stack/shared-values.yaml"):
            get_stack_cloud_provider("test-stack", mock_io_layer)
            
    def test_get_stack_cloud_provider_dev_mismatch(self):
        """Test error handling for dev stack cloud provider mismatch."""
        mock_io_layer = Mock()
        mock_io_layer.read_shared_values_yaml.return_value = {"cloudProvider": "azure"}
        
        with pytest.raises(ValueError, match="Dev stack dev-keboola-gcp-us-central1 cloud mismatch: expected gcp, found azure"):
            get_stack_cloud_provider("dev-keboola-gcp-us-central1", mock_io_layer)
            
    def test_classify_stacks_by_cloud(self):
        """Test stack classification by cloud provider."""
        mock_io_layer = Mock()
        
        def mock_shared_values(stack):
            cloud_mapping = {
                "dev-keboola-gcp-us-central1": {"cloudProvider": "gcp"},
                "kbc-testing-azure-east-us-2": {"cloudProvider": "azure"},
                "com-keboola-aws-prod": {"cloudProvider": "aws"},
            }
            return cloud_mapping.get(stack)
            
        mock_io_layer.read_shared_values_yaml.side_effect = mock_shared_values
        
        stacks = [
            "dev-keboola-gcp-us-central1",
            "kbc-testing-azure-east-us-2", 
            "com-keboola-aws-prod"
        ]
        
        result = classify_stacks_by_cloud(stacks, mock_io_layer)
        
        # Check structure
        assert "aws" in result
        assert "azure" in result
        assert "gcp" in result
        
        # Check GCP classification
        assert len(result["gcp"]) == 1
        gcp_stack = result["gcp"][0]
        assert gcp_stack.stack == "dev-keboola-gcp-us-central1"
        assert gcp_stack.cloud_provider == "gcp"
        assert gcp_stack.is_dev == True
        
        # Check Azure classification
        assert len(result["azure"]) == 1
        azure_stack = result["azure"][0]
        assert azure_stack.stack == "kbc-testing-azure-east-us-2"
        assert azure_stack.cloud_provider == "azure"
        assert azure_stack.is_dev == True
        
        # Check AWS classification
        assert len(result["aws"]) == 1
        aws_stack = result["aws"][0]
        assert aws_stack.stack == "com-keboola-aws-prod"
        assert aws_stack.cloud_provider == "aws"
        assert aws_stack.is_dev == False


