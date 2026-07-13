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

### Deploy strategies (production is always promoter-managed)

Every production/semver deploy is release-promoter-managed — HIU creates the PRs
**unmerged** (auto-approved) and never merges a PR that targets a production stack.
`DEPLOY_STRATEGY` (empty = `standard`) selects the fan-out: `standard` (2-wave
dev→prod), `gradual`/`critical`/`critical-manual-gate` (4 waves), or `manual-per-stack`
(one PR per stack). Non-production deploys (`dev-*`/`canary-*` tags, `override-stack`
targets) ignore `DEPLOY_STRATEGY` and are auto-merged by HIU. Auto-merge is decided by
tag class + target stacks — there is no `AUTOMERGE`/`MULTI_STAGE`/`cloud_multi_stage`
(removed in ST-4169/ST-4159).

### GitHub Actions Integration

The tool is designed to run as a GitHub Action via `action.yaml`. It:
- Installs HIU from the action's own checked-out source at the pinned ref (`$GITHUB_ACTION_PATH`), so `action.yaml` and the Python code always run in lockstep
- Sets up Python 3.13 environment
- Configures git with canary branch support
- Runs the update process with environment variables

## Key Environment Variables

- `HELM_CHART`: Name of the Helm chart to update (required)
- `IMAGE_TAG`: New image tag (must match specific prefixes)
- `GH_TOKEN`: GitHub access token (required)
- `DRY_RUN`: Perform dry run (default: "false")
- `DEPLOY_STRATEGY`: Rollout strategy (empty = `standard`): standard | gradual | critical | critical-manual-gate | manual-per-stack
- `OVERRIDE_STACK`: Target specific stack bypassing automatic selection
- `EXTRA_TAG1`, `EXTRA_TAG2`: Additional tags in format "path.in.yaml:value"

## Testing Strategy

The test suite covers:
- Development tag handling and single stack updates (auto-merged)
- Production tag handling: promoter-managed rollout (unmerged PRs), never merged by HIU
- Canary tag handling with stack-specific updates
- Deploy-strategy grouping (standard 2-wave, wave strategies, manual-per-stack)
- Git operations and PR creation
- Configuration validation and error handling

Tests use pytest with fixtures defined in `conftest.py` for common setup like mock repositories and GitHub clients.

## Development Workflow

When making changes, follow this workflow:

### 1. Run unit tests locally
```bash
pytest -sv tests/
```
All 74 tests must pass before proceeding.

### 2. Create a draft PR
```bash
gh pr create --draft --title "..." --body "..."
```
- Always use the PR template from `.github/pull_request_template.md`
- Always create as draft
- Include the Linear task ID (e.g. `[ST-XXXX]`) in the PR body

### 3. Trigger E2E tests
The E2E test suite lives in a separate repo (`keboola/helm-image-updater-testing`). Trigger it against your branch:
```bash
gh workflow run test-suite.yaml \
  --repo keboola/helm-image-updater-testing \
  --field helm-image-updater-branch=<your-branch-name>
```
Get the run URL:
```bash
gh run list --repo keboola/helm-image-updater-testing --workflow=test-suite.yaml --limit 1 --json url --jq '.[0].url'
```
Monitor until completion:
```bash
gh run view <run-id> --repo keboola/helm-image-updater-testing --json status,conclusion,jobs \
  --jq '{status: .status, conclusion: .conclusion, jobs: [.jobs[] | {name: .name, conclusion: .conclusion}]}'
```

### 4. Update PR with E2E results
Once the E2E suite passes, add the run link to the PR description under an `## E2E tests` section:
```bash
gh pr edit <pr-number> --body "...(updated body with E2E test link)..."
```

### 5. If E2E tests fail
- Check which test cases failed and read the logs
- Fix the code, push, and re-trigger the E2E suite
- Repeat until all 9 test scenarios pass