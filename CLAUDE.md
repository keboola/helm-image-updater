# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Helm Image Updater is a Python tool that automates image tag updates across Helm charts in different Kubernetes stacks. It creates pull requests for updates and can optionally auto-merge them. The tool is designed to work with three types of environments: development, production, and canary deployments.

## Development Commands

### Installation and Setup
```bash
# Create virtual environment
python -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
pip install -e .
```

### Testing
```bash
# Run all tests
pytest tests/

# Run tests with verbose output
pytest -sv tests/

# Run specific test file
pytest -sv tests/test_tag_updater.py

# Run with coverage
pytest --cov=helm_image_updater tests/
```

### Building and Distribution
```bash
# Build package
python setup.py sdist bdist_wheel

# Test installation locally
pip install -e .
```

### Running the Tool
```bash
# Basic usage (requires environment variables)
helm-image-updater

# With environment variables
HELM_CHART=dummy-service IMAGE_TAG=dev-b10536c41180e420eaf083451a1ddee132f512c6 GH_TOKEN=xxx helm-image-updater

# Dry run mode
DRY_RUN=true HELM_CHART=dummy-service IMAGE_TAG=dev-b10536c41180e420eaf083451a1ddee132f512c6 GH_TOKEN=xxx helm-image-updater
```

## Architecture

### Core Components

- **cli.py**: Main entry point that orchestrates the entire update process
- **tag_updater.py**: Core logic for updating tag.yaml files across different stacks
- **pr_manager.py**: Handles GitHub pull request creation and management
- **git_operations.py**: Git-related operations like branch creation and commits
- **config.py**: Configuration settings and constants (stack definitions, GitHub repo)
- **utils.py**: Utility functions for metadata handling and logging
- **exceptions.py**: Custom exception classes

### Tag Update Strategy

The tool determines update strategy based on image tag prefix:

1. **Development tags** (`dev-*`): Updates only development stacks defined in `DEV_STACKS`
2. **Production tags** (`production-*` or semver): Updates all non-canary stacks
3. **Canary tags** (`canary-orion-*`): Updates specific canary stacks with their own base branches

### Stack Management

- Stacks are organized as directories containing Helm chart configurations
- Each stack has a `{helm-chart}/tag.yaml` file that gets updated
- Stack types are defined in `config.py` as constants (`DEV_STACKS`, `CANARY_STACKS`)
- Ignored folders and excluded stacks are also configured in `config.py`

### Multi-Stage Deployment

When `MULTI_STAGE=true`:
1. Creates and auto-merges PR for dev stacks (if automerge is enabled)
2. Creates separate PR for production stacks (without auto-merge)

### GitHub Actions Integration

The tool is designed to run as a GitHub Action via `action.yaml`. It:
- Downloads the latest release package
- Sets up Python 3.13 environment
- Configures git with canary branch support
- Runs the update process with environment variables

## Key Environment Variables

- `HELM_CHART`: Name of the Helm chart to update (required)
- `IMAGE_TAG`: New image tag (must match specific prefixes)
- `GH_TOKEN`: GitHub access token (required)
- `AUTOMERGE`: Auto-merge PRs (default: "true")
- `DRY_RUN`: Perform dry run (default: "false")
- `MULTI_STAGE`: Enable multi-stage deployment (default: "false")
- `OVERRIDE_STACK`: Target specific stack bypassing automatic selection
- `EXTRA_TAG1`, `EXTRA_TAG2`: Additional tags in format "path.in.yaml:value"

## Testing Strategy

The test suite covers:
- Development tag handling and single stack updates
- Production tag handling with multiple stack updates
- Canary tag handling with stack-specific updates
- Multi-stage deployment scenarios
- Git operations and PR creation
- Configuration validation and error handling

Tests use pytest with fixtures defined in `conftest.py` for common setup like mock repositories and GitHub clients.