"""
Cloud Provider Detection Module

This module provides functionality to detect and classify cloud providers for stacks
based on their shared-values.yaml configuration and static development stack mapping.

Classes:
    StackCloudInfo: Data class containing stack cloud information

Functions:
    get_stack_cloud_provider: Detect cloud provider for a single stack
    classify_stacks_by_cloud: Group multiple stacks by cloud provider
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Protocol
from .config import DEV_STACK_MAPPING, SUPPORTED_CLOUD_PROVIDERS


class IOLayerProtocol(Protocol):
    """Protocol for IO layer operations needed by cloud detection."""
    
    def read_shared_values_yaml(self, stack: str) -> Optional[Dict]:
        """Read and parse shared-values.yaml for a stack."""
        ...


@dataclass
class StackCloudInfo:
    """Information about a stack's cloud provider and classification."""
    
    stack: str
    cloud_provider: str
    is_dev: bool


def get_stack_cloud_provider(stack: str, io_layer: IOLayerProtocol) -> str:
    """Get cloud provider for any stack with strict validation.
    
    Args:
        stack: Stack name to check
        io_layer: IO layer instance for reading files
        
    Returns:
        Cloud provider name (aws, azure, or gcp)
        
    Raises:
        ValueError: If cloudProvider is missing, invalid, or inconsistent with dev mapping
    """
    # For dev stacks: check static mapping
    dev_cloud = None
    for cloud, dev_stack in DEV_STACK_MAPPING.items():
        if stack == dev_stack:
            dev_cloud = cloud
            break
    
    # Read shared-values.yaml for all stacks (validation for dev, requirement for prod)
    shared_values = io_layer.read_shared_values_yaml(stack)
    if not shared_values or 'cloudProvider' not in shared_values:
        raise ValueError(f"Missing cloudProvider in {stack}/shared-values.yaml")
    
    yaml_cloud = shared_values['cloudProvider']
    if yaml_cloud not in SUPPORTED_CLOUD_PROVIDERS:
        raise ValueError(f"Unsupported cloudProvider '{yaml_cloud}' in {stack}/shared-values.yaml")
    
    # For dev stacks: validate consistency between mapping and YAML
    if dev_cloud and dev_cloud != yaml_cloud:
        raise ValueError(f"Dev stack {stack} cloud mismatch: expected {dev_cloud}, found {yaml_cloud}")
    
    return yaml_cloud


def classify_stacks_by_cloud(stacks: List[str], io_layer: IOLayerProtocol) -> Dict[str, List[StackCloudInfo]]:
    """Group stacks by cloud provider with dev/prod classification.
    
    Args:
        stacks: List of stack names to classify
        io_layer: IO layer instance for reading files
        
    Returns:
        Dictionary mapping cloud providers to lists of StackCloudInfo objects
        
    Raises:
        ValueError: If any stack has invalid cloud provider configuration
    """
    result = {"aws": [], "azure": [], "gcp": []}
    
    for stack in stacks:
        cloud = get_stack_cloud_provider(stack, io_layer)
        is_dev = stack in DEV_STACK_MAPPING.values()
        
        result[cloud].append(StackCloudInfo(
            stack=stack,
            cloud_provider=cloud,
            is_dev=is_dev
        ))
    
    return result