"""
Tag Classification Module

Pure functions for detecting and validating image tag types.
This module contains no side effects - only tag analysis logic.
"""

import re
from enum import Enum
from typing import Optional
from dataclasses import dataclass

from .config import CANARY_STACKS


class TagType(Enum):
    """Enum for different tag types."""
    DEV = "dev"
    PRODUCTION = "production"
    CANARY = "canary"
    SEMVER = "semver"
    INVALID = "invalid"


def detect_tag_type(tag: str) -> TagType:
    """
    Determine the type of a tag based on its format.
    
    Pure function that classifies a tag without any I/O.
    
    Args:
        tag: The image tag string
        
    Returns:
        TagType enum value
    """
    if not tag or not tag.strip():
        return TagType.INVALID
    
    tag = tag.strip()
    
    # Check for dev tag
    if tag.startswith("dev-"):
        return TagType.DEV
    
    # Check for production tag
    if tag.startswith("production-"):
        return TagType.PRODUCTION
    
    # Check for canary tags
    for prefix in CANARY_STACKS.keys():
        if tag.startswith(prefix):
            return TagType.CANARY
    
    # Check for semver format (e.g., 1.2.3 or v1.2.3)
    if re.match(r"^v?\d+\.\d+\.\d+$", tag):
        return TagType.SEMVER
    
    return TagType.INVALID