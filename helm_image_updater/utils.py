"""
Utility Functions Module for Helm Image Updater

This module provides various utility functions used throughout the application.
It includes helpers for logging, generating random values, and handling
workflow trigger metadata.

Functions:
    setup_logging: Configures application logging
    random_suffix: Generates random string suffixes for branch names
    get_trigger_metadata: Retrieves and decodes workflow trigger metadata
    print_dry_run_summary: Displays a summary of changes for dry runs

The module serves as a collection of helper functions that don't fit
into other more specific modules.
"""

import base64
import json
import os
import random
import string
import logging

logger = logging.getLogger(__name__)


def setup_logging(level: int = logging.INFO) -> None:
    """Configure logging for the application."""
    logging.basicConfig(
        level=level, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )


def random_suffix(length=4):
    """Generate a random string suffix."""
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=length))


def get_trigger_metadata() -> dict:
    """Get metadata about what triggered this workflow.

    Returns:
        dict: Decoded metadata about the trigger source or empty dict if not available
    """
    encoded_metadata = os.environ.get("METADATA")
    if not encoded_metadata:
        return {}

    try:
        decoded = base64.b64decode(encoded_metadata).decode("utf-8")
        return json.loads(decoded)
    except (base64.binascii.Error, UnicodeDecodeError, json.JSONDecodeError) as e:
        print(f"Warning: Failed to decode metadata: {e}")
        return {}


def print_dry_run_summary(changes, missing_tags):
    """Print a summary of changes that would be made in dry run mode."""
    print("\nDry run summary:")
    print("Changes that would be made:")
    for change in changes:
        print(f"- Stack: {change['stack']}")
        print(f"  Chart: {change['chart']}")
        print(f"  Tag: {change['tag']}")
        print(f"  Auto-merge: {change['automerge']}")

    if missing_tags:
        print("\nMissing tag.yaml files:")
        for missing in missing_tags:
            print(f"- {missing}")
