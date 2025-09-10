"""
Cloud Provider Detection Module

This module provides functionality to detect cloud providers for stacks
based on their shared-values.yaml configuration.

Functions:
    get_stack_cloud_provider: Detect cloud provider for a single stack
"""

from typing import Dict, Optional, Protocol
from .config import DEV_STACK_MAPPING, SUPPORTED_CLOUD_PROVIDERS


class IOLayerProtocol(Protocol):
    """Protocol for IO layer operations needed by cloud detection."""
    
    def read_shared_values_yaml(self, stack: str) -> Optional[Dict]:
        """Read and parse shared-values.yaml for a stack."""
        ...


def get_stack_cloud_provider(stack: str, io_layer: IOLayerProtocol) -> str:
    """Get cloud provider for any stack with strict validation.
    
    Args:
        stack: Stack name to check
        io_layer: IO layer instance for reading files
        
    Returns:
        Cloud provider name (aws, azure, or gcp)
        
    Raises:
        ValueError: If cloudProvider is missing or invalid.
    """
    
    # Read shared-values.yaml for all stacks (validation for dev, requirement for prod)
    shared_values = io_layer.read_shared_values_yaml(stack)
    if not shared_values or 'cloudProvider' not in shared_values:
        raise ValueError(f"Missing cloudProvider in {stack}/shared-values.yaml")
    
    yaml_cloud = shared_values['cloudProvider']
    if yaml_cloud not in SUPPORTED_CLOUD_PROVIDERS:
        raise ValueError(f"Unsupported cloudProvider '{yaml_cloud}' in {stack}/shared-values.yaml")
    
    return yaml_cloud


