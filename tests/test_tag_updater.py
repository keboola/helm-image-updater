"""Test module for tag_updater.py.

This module contains tests for the tag updating functionality of the Helm Image Updater.
It verifies the proper handling of tag.yaml files and image tag updates.

The tests use temporary directories to simulate stack structures and verify
that tag updates are performed correctly in various scenarios.

Test Cases:
    test_update_tag_yaml: Verifies successful tag updates
    test_update_tag_yaml_missing_file: Verifies handling of missing files
"""

import pytest
import yaml
from helm_image_updater.tag_updater import update_tag_yaml


def test_update_tag_yaml(tmp_path):
    """Tests successful tag.yaml file updates.

    This test verifies that update_tag_yaml correctly:
    1. Creates necessary directory structure
    2. Updates the image tag in tag.yaml
    3. Preserves file structure while updating

    Args:
        tmp_path (Path): Pytest fixture providing temporary directory path

    Returns:
        None

    Raises:
        AssertionError: If tag update fails or produces incorrect results

    Example:
        A successful test will verify:
            * tag.yaml file is created with initial tag
            * Tag is updated to new value
            * File structure remains intact
    """
    # Create a test stack structure
    stack_dir = tmp_path / "test-stack"
    stack_dir.mkdir()
    chart_dir = stack_dir / "test-chart"
    chart_dir.mkdir()

    # Create a test tag.yaml
    tag_file = chart_dir / "tag.yaml"
    initial_data = {"image": {"tag": "old-tag"}}
    with tag_file.open("w") as f:
        yaml.dump(initial_data, f)

    # Test updating the tag
    result = update_tag_yaml(stack_dir, "test-chart", "new-tag", dry_run=False)

    assert result is True

    # Verify the update
    with tag_file.open() as f:
        updated_data = yaml.safe_load(f)
    assert updated_data["image"]["tag"] == "new-tag"


def test_update_tag_yaml_missing_file(tmp_path):
    """Tests handling of missing tag.yaml files.

    This test verifies that update_tag_yaml correctly:
    1. Handles non-existent chart directories
    2. Returns None for missing tag.yaml files

    Args:
        tmp_path (Path): Pytest fixture providing temporary directory path

    Returns:
        None

    Raises:
        AssertionError: If missing file handling is incorrect

    Example:
        A successful test will verify:
            * None is returned when file doesn't exist
            * No errors are raised for missing files
    """
    result = update_tag_yaml(tmp_path, "nonexistent-chart", "new-tag")
    assert result is None
