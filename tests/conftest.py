"""Test fixtures for Helm Image Updater.

This module provides shared fixtures used across multiple test modules.
It sets up common test data structures and mock objects that simulate
the environment needed for testing.

Fixtures:
    sample_tag_yaml: Creates a temporary tag.yaml file structure
"""

import pytest
import yaml


@pytest.fixture
def sample_tag_yaml(tmp_path):
    """Creates a temporary tag.yaml file structure for testing.

    This fixture sets up a mock directory structure that mimics a real stack:
    tmp_path/
    └── test-stack/
        └── test-chart/
            └── tag.yaml

    The tag.yaml file contains a basic image tag configuration.

    Args:
        tmp_path (Path): Built-in pytest fixture providing a temporary directory path

    Returns:
        dict: A dictionary containing:
            - stack_dir (Path): Path to the test stack directory
            - chart_dir (Path): Path to the test chart directory
            - tag_file (Path): Path to the tag.yaml file
            - initial_data (dict): The initial tag.yaml content

    Example:
        When used in a test, this fixture provides:
            * A temporary directory structure
            * A tag.yaml file with initial content
            * Access to all created paths and initial data
    """
    stack_dir = tmp_path / "test-stack"
    stack_dir.mkdir()
    chart_dir = stack_dir / "test-chart"
    chart_dir.mkdir()

    tag_file = chart_dir / "tag.yaml"
    data = {"image": {"tag": "initial-tag"}}

    with tag_file.open("w") as f:
        yaml.dump(data, f)

    return {
        "stack_dir": stack_dir,
        "chart_dir": chart_dir,
        "tag_file": tag_file,
        "initial_data": data,
    }
