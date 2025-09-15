"""
Stack Classification Module

Pure functions for classifying and filtering stacks.
This module contains no side effects - only stack analysis logic.
"""

from dataclasses import dataclass
from typing import List

from .config import DEV_STACK_MAPPING, CANARY_STACKS, IGNORED_FOLDERS, EXCLUDED_STACKS


@dataclass
class StackClassification:
    """Classification of a stack."""
    name: str
    is_dev: bool
    is_production: bool
    is_canary: bool
    is_excluded: bool
    is_ignored: bool


def classify_stack(stack_name: str) -> StackClassification:
    """
    Classify a stack based on its name and configuration.
    
    Pure function that determines stack properties.
    
    Args:
        stack_name: Name of the stack directory
        
    Returns:
        StackClassification object with stack properties
    """
    # Get list of canary stack names
    canary_stack_names = [info["stack"] for info in CANARY_STACKS.values()]
    
    # Get list of dev stack names from mapping
    dev_stack_names = list(DEV_STACK_MAPPING.values())
    
    return StackClassification(
        name=stack_name,
        is_dev=stack_name in dev_stack_names,
        is_production=(
            stack_name not in IGNORED_FOLDERS
            and stack_name not in EXCLUDED_STACKS
            and stack_name not in dev_stack_names
            and stack_name not in canary_stack_names
        ),
        is_canary=stack_name in canary_stack_names,
        is_excluded=stack_name in EXCLUDED_STACKS,
        is_ignored=stack_name in IGNORED_FOLDERS
    )


def get_dev_stacks(all_stacks: List[str]) -> List[str]:
    """
    Get development stacks, excluding ignored/excluded ones.
    
    Args:
        all_stacks: List of all stack names
        
    Returns:
        Filtered list of development stack names
    """
    result = []
    for stack in all_stacks:
        classification = classify_stack(stack)
            
        if classification.is_dev:
            result.append(stack)
    
    return result