# Helm Image Updater

A Python tool for automating image tag updates across Helm charts in different Kubernetes stacks. This tool creates pull requests for updates and can optionally auto-merge them.

## Features

- Updates image tags in Helm charts across multiple stacks
- Supports both development and production deployments
- Creates pull requests with detailed descriptions
- Optional auto-merge functionality
- Dry-run mode for testing changes
- Multi-stage deployment support
- Extra tag updates for complex configurations
- Detailed logging and error handling

## Installation

Clone the repository:

    git clone https://github.com/helm-image-updater/helm-image-updater.git
    cd helm-image-updater

Install the package:

    pip install -e .

## Usage

The tool can be run either directly or through GitHub Actions.

### Environment Variables

Required:

- `HELM_CHART`: Name of the Helm chart to update
- `GH_TOKEN`: GitHub access token for authentication
- `IMAGE_TAG`: New image tag to set (must start with 'dev-' or 'production-')

Optional:

- `AUTOMERGE`: Whether to automatically merge PRs (default: "true")
- `DRY_RUN`: Whether to perform a dry run (default: "false")
- `MULTI_STAGE`: Enable multi-stage deployment (default: "false")
- `TARGET_PATH`: Path to the directory containing the stacks (default: ".")
- `EXTRA_TAG1`, `EXTRA_TAG2`: Additional tags to update (format: "path:value")

### Command Line Examples

Basic usage:
    HELM_CHART=my-chart IMAGE_TAG=dev-1.0.0 GH_TOKEN=xxx helm-image-updater

With extra tags:
    HELM_CHART=my-chart \
    IMAGE_TAG=dev-1.0.0 \
    EXTRA_TAG1="agent.image.tag:dev-2.0.0" \
    GH_TOKEN=xxx \
    helm-image-updater

Dry run:
    DRY_RUN=true HELM_CHART=my-chart IMAGE_TAG=dev-1.0.0 GH_TOKEN=xxx helm-image-updater

### Docker Usage

    docker run --rm \
      -e HELM_CHART=my-chart \
      -e IMAGE_TAG=dev-1.0.0 \
      -e GH_TOKEN=xxx \
      ghcr.io/yourusername/helm-image-updater:latest

## Tag Format

Image tags must follow these formats:

- Development: `dev-*` (e.g., `dev-1.0.0`)
- Production: `production-*` (e.g., `production-1.0.0`)

## Stack Types

- **Development Stacks**: Only updated with `dev-` tags
- **Production Stacks**: Updated with `production-` tags

## Multi-Stage Deployment

When `MULTI_STAGE=true`:

1. Creates and auto-merges PR for dev stacks
2. Creates a separate PR (without auto-merge) for production stacks

## Development

Create virtual environment:
    python -m venv .venv
    source .venv/bin/activate  # On Windows: .venv\Scripts\activate

Install dependencies:
    pip install -r requirements.txt
    pip install -e .

Run tests:
    pip install pytest
    pytest tests/

## Testing

The test suite verifies the following functionality:

### Tag Update Strategies

- Development tag handling (`dev-*`)

  - Single stack updates
  - Automerge functionality
  - Tag file modifications

- Production tag handling (`production-*`)

  - Multiple stack updates
  - Multi-stage deployment support
  - Concurrent stack updates

### Test Configuration

Run the tests with:

    pytest -sv tests/

To see detailed test execution logs:

    pytest -sv tests/test_tag_updater.py

<!-- ## Project Structure

helm-image-updater/
├── helm_image_updater/
│   ├── __init__.py
│   ├── cli.py           # Main entry point
│   ├── config.py        # Configuration settings
│   ├── exceptions.py    # Custom exceptions
│   ├── git_operations.py # Git-related operations
│   ├── pr_manager.py    # Pull request management
│   ├── tag_updater.py   # Core tag update logic
│   └── utils.py         # Utility functions
├── tests/
│   └── test_tag_updater.py
├── requirements.txt
└── setup.py -->
