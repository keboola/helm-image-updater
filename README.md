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

### From GitHub Release

You can install the latest release directly from GitHub:

    pip install https://github.com/keboola/helm-image-updater/releases/latest/download/helm_image_updater-*.tar.gz

Or a specific version:

    pip install https://github.com/keboola/helm-image-updater/releases/download/v0.0.1/helm_image_updater-0.0.1.tar.gz

### From Source

Clone the repository:

    git clone https://github.com/keboola/helm-image-updater.git
    cd helm-image-updater

Install the package:

    pip install -e .

## Usage

The tool can be run either directly or through GitHub Actions.

### Environment Variables

Required:

- `HELM_CHART`: Name of the Helm chart to update
- `GH_TOKEN`: GitHub access token for authentication
- `IMAGE_TAG`: New image tag to set (must start with 'dev-', 'production-', or 'canary-orion-')

Optional:

- `AUTOMERGE`: Whether to automatically merge PRs (default: "true", note: canary updates are always auto-merged)
- `DRY_RUN`: Whether to perform a dry run (default: "false")
- `MULTI_STAGE`: Enable multi-stage deployment (default: "false")
- `TARGET_PATH`: Path to the directory containing the stacks (default: ".")
- `OVERRIDE_STACK`: Stack ID to explicitly target for the update, bypassing automatic stack selection (default: None)
- `EXTRA_TAG1`, `EXTRA_TAG2`: Additional tags to update (format: "path:value")

### Command Line Examples

Basic usage:

    HELM_CHART=dummy-service IMAGE_TAG=dev-b10536c41180e420eaf083451a1ddee132f512c6 GH_TOKEN=xxx helm-image-updater

With extra tags:

    HELM_CHART=dummy-service \
    IMAGE_TAG=dev-b10536c41180e420eaf083451a1ddee132f512c6 \
    EXTRA_TAG1="agent.image.tag:dev-2.0.0" \
    GH_TOKEN=xxx \
    helm-image-updater

Dry run:

    DRY_RUN=true HELM_CHART=dummy-service IMAGE_TAG=dev-b10536c41180e420eaf083451a1ddee132f512c6 GH_TOKEN=xxx helm-image-updater

### GitHub Actions

Minimal usage example:

```yaml
- uses: "keboola/helm-image-updater@main"
  with:
    helm-chart: "dummy-service"
    image-tag: "dev-b10536c41180e420eaf083451a1ddee132f512c6"
    automerge: "true"
    dry-run: "false"
    multi-stage: "false"
    override-stack: "dev-keboola-gcp-us-central1"
    extra-tag1: "agent.image.tag:dev-2.0.0"
    extra-tag2: "messenger.image.tag:dev-2.0.0"
```

Full example:

```yaml
name: "Update service image tag"
run-name: "Update image tag ${{ inputs.helm-chart }}@${{ inputs.image-tag }} ${{ inputs.extra-tag1 }} ${{ inputs.extra-tag2 }}"
on:
  workflow_dispatch:
    inputs:
      helm-chart:
        description: "The Helm chart to update"
        required: true
        type: string
      image-tag:
        description: "Updates image.tag in tag.yaml If not set, extra tags must be specified."
        required: false
        type: string
        default: ''
      automerge:
        description: "Automatically merge PRs"
        required: false
        type: boolean
        default: true
      dry-run:
        description: "Do a dry run"
        required: false
        type: boolean
        default: false
      multi-stage:
        description: "Enable multi-stage deployment (auto-merge dev, manual prod)"
        required: false
        type: boolean
        default: false
      override-stack:
        description: "Stack ID to explicitly target for the update, bypassing automatic stack selection."
        required: false
        type: string
        default: ''
      extra-tag1:
        description: "Extra tag 1 to update to tag.yaml, value in format path.in.yaml:value"
        required: false
        type: string
        default: ''
      extra-tag2:
        description: "Extra tag 2 to update to tag.yaml, value in format path.in.yaml:value"
        required: false
        type: string
        default: ''
      metadata:
        description: "Base64 encoded JSON with trigger metadata"
        required: false
        type: string
        default: ''

jobs:
  update-helm-chart-image-tag:
    name: Update image tag ${{ inputs.helm-chart }}@${{ inputs.image-tag }}
    runs-on: ubuntu-latest
    steps:
      - name: Generate a token
        id: app-token
        uses: actions/create-github-app-token@v1.11.0
        with:
          app-id: "1032649"
          private-key: ${{ secrets.GITOPS_KBC_STACKS_ACTIONS_APP_PVK }}
          owner: ${{ github.repository_owner }}
          repositories: "sre-playground"

      - uses: actions/checkout@v4.2.1
        name: Checkout repository
        with:
          token: ${{ steps.app-token.outputs.token }}
          ref: "main"

      - uses: qoomon/actions--setup-git@v1.1.1
        with:
          user: bot

      - uses: keboola/helm-image-updater@main
        with:
          helm-chart: ${{ inputs.helm-chart }}
          image-tag: ${{ inputs.image-tag }}
          automerge: ${{ inputs.automerge }}
          dry-run: ${{ inputs.dry-run }}
          multi-stage: ${{ inputs.multi-stage }}
          override-stack: ${{ inputs.override-stack }}
          extra-tag1: ${{ inputs.extra-tag1 }}
          extra-tag2: ${{ inputs.extra-tag2 }}
          metadata: ${{ inputs.metadata }}
          github-token: ${{ steps.app-token.outputs.token }}

## Tag Format

Image tags must follow these formats:

- Development: `dev-*` (e.g., `dev-b10536c41180e420eaf083451a1ddee132f512c6`)
- Production: `production-*` (e.g., `production-b10536c41180e420eaf083451a1ddee132f512c6`)
- Canary: `canary-*` (e.g., `canary-orion-b10536c41180e420eaf083451a1ddee132f512c6`)

## Stack Types

- **Development Stacks**: Only updated with `dev-` tags
- **Production Stacks**: Updated with `production-` tags
- **Canary Stacks**: Updated with `canary-*` tags, always auto-merged and target their specific base branches

## Stack Update Behavior

### Development Updates

- Updates only development stacks
- Auto-merge behavior follows the `automerge` setting

### Production Updates

- Updates production stacks
- Auto-merge behavior follows the `automerge` setting
- In multi-stage mode, creates separate PRs for dev and prod stacks

### Canary Updates

- Updates only the specific canary stack matching the tag prefix
- Always auto-merges regardless of the `automerge` setting
- Uses stack-specific base branches (e.g., `canary-orion`)
- Supports extra tags for complex configurations

## Multi-Stage Deployment

When `MULTI_STAGE=true`:

1. Creates and auto-merges PR for dev stacks (if automerge is also true, otherwise won't merge the dev PR)
2. Creates a separate PR (without auto-merge) for production stacks

## Development

Create virtual environment:

    python -m venv .venv
    source .venv/bin/activate

Install dependencies:

    pip install -r requirements.txt
    pip install -e .

Run tests:

    pip install pytest
    pytest tests/

### Testing Changes in sre-playground

To test your changes in the real environment (sre-playground repository) before creating a release:

1. **Modify action.yaml** to use source code instead of release:

   ```yaml
   # Comment out the release downloader
   # - uses: robinraju/release-downloader@v1.9
   #   with:
   #     repository: "keboola/helm-image-updater"
   #     latest: true

   # Add source checkout and installation
   - name: Checkout helm-image-updater source
     uses: actions/checkout@v4
     with:
       repository: keboola/helm-image-updater
       ref: your-feature-branch-name  # Replace with your branch
       path: helm-image-updater-src

   - name: Install helm-image-updater from source
     shell: bash
     run: |
       echo "Installing helm-image-updater from source code..."
       cd helm-image-updater-src
       pip install -e .
   ```

   And update the final step to remove the pip install line:

   ```yaml
   - name: Update image tags and create PRs
     shell: bash
     env:
       # ... environment variables ...
     run: |
       # pip install helm_image_updater-*.tar.gz  # Comment this out
       helm-image-updater
   ```

2. **Commit and push your feature branch** with the changes

3. **In sre-playground**, update the workflow to use your branch:

   ```yaml
   - uses: keboola/helm-image-updater@your-feature-branch
     with:
       helm-chart: metastore
       image-tag: canary-orion-metastore-0.0.5
       github-token: ${{ secrets.GITHUB_TOKEN }}
   ```

4. **Test your changes** by running the workflow in sre-playground

5. **When satisfied**, revert the action.yaml changes and create a proper release

This approach allows you to test fixes in the "real" environment without needing to create releases for testing purposes.

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

- Canary tag handling (`canary-*`)

  - Stack-specific updates
  - Automatic auto-merge
  - Base branch targeting
  - Extra tag support

### Test Configuration

Run the tests with:

    pytest -sv tests/

To see detailed test execution logs:

    pytest -sv tests/test_tag_updater.py

## Project Structure

    helm-image-updater/
    ├── helm_image_updater/
    │   ├── __init__.py
    │   ├── cli.py              # Main entry point
    │   ├── config.py           # Configuration settings
    │   ├── exceptions.py       # Custom exceptions
    │   ├── git_operations.py   # Git-related operations
    │   ├── pr_manager.py       # Pull request management
    │   ├── tag_updater.py      # Core tag update logic
    │   └── utils.py            # Utility functions
    ├── tests/
    │   ├── conftest.py         # Pytest configuration and fixtures
    │   ├── test_git_operations.py   # Tests for Git operations
    │   ├── test_pr_manager.py      # Tests for PR creation and management
    │   └── test_tag_updater.py     # Tests for tag update logic
    ├── requirements.txt
    └── setup.py


### e2e tests
You can test a branch from this repo via running e2e tests in https://github.com/keboola/helm-image-updater-testing/actions/workflows/test-suite.yaml - just trigger the "Test suite" workflow and provide name of branch from this repo.