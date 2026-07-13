# Helm Image Updater

A Python tool for automating image tag updates across Helm charts in different Kubernetes stacks. This tool creates pull requests for updates and can optionally auto-merge them.

## Features

- Updates image tags in Helm charts across multiple stacks
- Supports both development and production deployments
- Creates pull requests with detailed descriptions
- Production deploys are always release-promoter-managed; dev/canary/override deploys are auto-merged fast by HIU
- Dry-run mode for testing changes
- Promoter rollout strategies (standard 2-wave, gradual/critical waves, manual-per-stack)
- Extra tag updates for complex configurations
- Detailed logging and error handling
- Automatically removes ArgoCD branch overrides (`appManifestsRevision`) from `values.yaml` in the same PR

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
- `GH_APPROVE_TOKEN`: Fine-grained GitHub PAT for the machine user `keboola-sre-approve-bot` used to auto-approve PRs (required). The token requires **Write** permission on **Pull Requests** for the managed repositories (`kbc-stacks`, `helm-image-updater-testing`). Credentials are stored in 1Password Ultra vault.
- `IMAGE_TAG`: New image tag to set (must start with 'dev-', 'production-', or 'canary-orion-')

Optional:

- `DRY_RUN`: Whether to perform a dry run (default: "false")
- `DEPLOY_STRATEGY`: Rollout strategy (default/empty: `standard`): `standard`, `gradual`, `critical`, `critical-manual-gate`, or `manual-per-stack`. See [Deploy Strategies](#deploy-strategies). Whether a PR auto-merges is decided by tag class + target stacks (see [Auto-merge](#auto-merge-and-auto-approve)), not by a knob.
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

Minimal usage example (always pin to a released version — see [Releasing](#releasing); the action runs the code from the pinned tag):

```yaml
- uses: "keboola/helm-image-updater@v0.23.0"
  with:
    helm-chart: "dummy-service"
    image-tag: "dev-b10536c41180e420eaf083451a1ddee132f512c6"
    dry-run: "false"
    deploy-strategy: ""  # empty = standard | gradual | critical | critical-manual-gate | manual-per-stack
    override-stack: "dev-keboola-gcp-us-central1"
    extra-tag1: "agent.image.tag:dev-2.0.0"
    extra-tag2: "messenger.image.tag:dev-2.0.0"
    approve-token: ${{ secrets.SRE_APPROVE_BOT_TOKEN }}
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
      dry-run:
        description: "Do a dry run"
        required: false
        type: boolean
        default: false
      deploy-strategy:
        description: "Rollout strategy (empty = standard): standard | gradual | critical | critical-manual-gate | manual-per-stack"
        required: false
        type: string
        default: ''
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

      - uses: keboola/helm-image-updater@v0.23.0
        with:
          helm-chart: ${{ inputs.helm-chart }}
          image-tag: ${{ inputs.image-tag }}
          dry-run: ${{ inputs.dry-run }}
          deploy-strategy: ${{ inputs.deploy-strategy }}
          override-stack: ${{ inputs.override-stack }}
          extra-tag1: ${{ inputs.extra-tag1 }}
          extra-tag2: ${{ inputs.extra-tag2 }}
          metadata: ${{ inputs.metadata }}
          github-token: ${{ steps.app-token.outputs.token }}
          approve-token: ${{ secrets.SRE_APPROVE_BOT_TOKEN }}

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

- Updates only development (and, via `override-stack`, e2e) stacks
- Auto-merged fast by HIU (a dev/e2e target is never production)

### Production Updates

- Updates all stacks the production tag lands on (dev + prod)
- Always release-promoter-managed: HIU creates the PRs **unmerged** (and auto-approved); release-promoter merges them. HIU never merges a PR that targets a production stack.
- Fanned out per the selected strategy — `standard` (2-wave dev→prod, the default), the wave strategies, or `manual-per-stack`

### Canary Updates

- Updates only the specific canary stack matching the tag prefix
- Always auto-merged by HIU (targets its own `canary-*` branch)
- Uses stack-specific base branches (e.g., `canary-orion`)
- Supports extra tags for complex configurations

## Auto-merge and Auto-approve

HIU decides whether to **merge** a PR itself purely from the **effective tag class** (across `image_tag` + extra tags) and the **target stacks** — there is no `AUTOMERGE` knob (ST-4169/ST-4159):

- **Production/semver-class** deploy, or any PR whose targets include a production stack → **not merged** by HIU. It is created unmerged and **auto-approved** with `GH_APPROVE_TOKEN` (machine user `keboola-sre-approve-bot`, satisfying CODEOWNERS) so release-promoter — or a human, for `manual-per-stack` — can merge it.
- **Dev / canary / `pr-test-*` (non-production-class)** deploy to non-production stacks (dev, e2e, canary, override) → **auto-merged** by HIU immediately.

Auto-approve and merge are both skipped during dry runs. `wave` and `manual-per-stack` PRs are always created unmerged regardless of tag class.

## Override Removal

When updating image tags, the tool automatically checks each target stack's `{helm-chart}/values.yaml` for ArgoCD branch overrides and removes them in the same PR.

**What gets removed:** The `argocdApplication.appManifestsRevision` field, when set to anything other than `"main"`. Developers sometimes set this to a feature branch for testing, but leaving it in place causes ArgoCD to keep deploying from that branch, silently ignoring any new image tag updates.

**What is preserved:** All other fields in `values.yaml` remain untouched. If removing `appManifestsRevision` leaves the `argocdApplication` block empty, the entire block is removed. If the file becomes empty as a result, an empty file is written.

**No configuration required** — this behavior is always active and runs automatically for every stack update.

**Visibility:** When overrides are removed, the PR body includes a "⚠️ Removed Branch Overrides" section listing exactly which files were modified.

Example `values.yaml` before update:

```yaml
argocdApplication:
  appManifestsRevision: my-feature-branch
  someOtherField: value
```

After update (override removed, other fields preserved):

```yaml
argocdApplication:
  someOtherField: value
```

If `argocdApplication` only contained `appManifestsRevision`, the entire block is removed:

```yaml
# (empty file)
```

## Deploy Strategies

`DEPLOY_STRATEGY` (action input `deploy-strategy`) selects how a **production** update is fanned out into PRs. Every production deploy is release-promoter-managed — HIU creates the PRs unmerged and the promoter (or, for `manual-per-stack`, a human) merges them. An **empty** `DEPLOY_STRATEGY` resolves to `standard`, the universal default (ST-4131/ST-4159). There is no `cloud_multi_stage`, `MULTI_STAGE`, or `AUTOMERGE` any more.

| `DEPLOY_STRATEGY` | grouping | who merges |
|---|---|---|
| empty → `standard` | promoter-managed **2-wave dev→prod**: wave 0 = all dev stacks (anchor, carries the manifest), wave 1 = all prod stacks | release-promoter merges dev → prod |
| `gradual` · `critical` · `critical-manual-gate` | 4 unmerged **wave** PRs (waves 0–3) | release-promoter merges wave-by-wave |
| `manual-per-stack` | **one PR per stack (dev + prod), no waves** | a human merges each in any order; release-promoter completes |

Non-production deploys (`dev-*` / `canary-*` tags and `override-stack` targets) ignore `DEPLOY_STRATEGY` — they stay single-PR deploys and are auto-merged by HIU (see [Auto-merge](#auto-merge-and-auto-approve)).

### Promoter-managed `standard` (2-wave dev→prod)

A **`production-`/semver tag** with `DEPLOY_STRATEGY` resolving to `standard` (i.e. the default — empty or explicit) emits the app as a promoter-managed **2-wave** release: **wave 0 = all dev stacks** (the anchor — it carries the JSON release manifest), **wave 1 = all prod stacks** (the positive `is_production` set, so canary/excluded stacks are never mis-binned into prod). The cloud dimension is collapsed (no per-cloud split). Both PRs are created unmerged and labelled `release:wave:{0,1}` + `deploy:standard`. [release-promoter](https://github.com/keboola/release-promoter) merges the dev wave, waits for its ArgoCD **sync** (no UAT, no soak), then merges the prod wave. An app present in only one tier (no dev stacks → 1-wave prod) degenerates to a **single-wave** release (wave 0 only), which the promoter handles count-agnostically. Identity is `instanceId = <app>-<image_tag>` (no cloud suffix), derived from the deployed tag(s) — the image tag, or for an `image.tag`-untouched deploy the extra tag(s) as `<path>=<value>` — so each build is unique; a duplicate fan-out for the same `instanceId` is refused while an anchor is still open.

Only **`PRODUCTION`** deploys are staged: a `dev-*` tag (DEV), a `canary-*` tag, and an `override-stack` deploy are **not** promoter-managed — they keep their existing handling (a dev push stays a fast, auto-merged deploy; canary auto-merges to its own branch; override is a single PR). This keeps routine dev deploys from being turned into unmerged wave PRs the promoter must merge. HIU has **no code path** that merges a PR whose targets include a production stack.

### Wave strategies (release-promoter)

`gradual`, `critical`, and `critical-manual-gate` emit 4 unmerged PRs labeled `release:wave:<0-3>` and `deploy:<strategy>`. Release grouping/identity is carried by a JSON **release manifest** that helm-image-updater writes into the wave-0 anchor PR body (machine-read by release-promoter); the legacy `release:id` label is retired. [release-promoter](https://github.com/keboola/release-promoter) merges them wave-by-wave (0 → 3) as each wave syncs, passes UAT, and soaks; `critical-manual-gate` additionally waits for a human gate before the later waves. helm-image-updater itself never merges wave PRs (it still auto-approves them).

### Manual-per-stack (one PR per stack)

An **explicit** `DEPLOY_STRATEGY=manual-per-stack` on a **`production-`/semver tag** emits a deliberately **order-independent** release: **one unmerged PR per stack** (no waves), each labelled `deploy:manual-per-stack` and auto-approved. Members are **every stack the production tag lands on — dev AND prod** (a production tag deploys to dev stacks too; only prod stacks are tag-restricted), excluding canary/e2e. A human merges each member PR in **any order**; [release-promoter](https://github.com/keboola/release-promoter) completes the release once **all** members are merged + synced (no sequencing, soak, UAT, or out-of-order hole). The **anchor** = the **lowest-numbered member PR**: it carries the `release:anchor` discovery label and the JSON **release manifest** (`mode:"manual-per-stack"` + the flat `members` list). Only `PRODUCTION` deploys are managed (`override-stack`/`canary`/`dev-*` keep their own handling). Identity is `instanceId = <app>-<image_tag>` (derived from the deployed tag(s) — see the `standard` section); a duplicate fan-out for the same `instanceId` is refused while an anchor is still open (the rerun guard scans both `release:wave:0` and `release:anchor` anchors).

> **Prerequisite (wave strategies):** the deploy token needs `Issues: write` (PR labels go through the Issues API), and release-promoter's merge identity needs `Contents: write` + `Pull requests: write` and must be a bypass actor on the target branch's ruleset.

## Releasing

A release ships **one coherent version**: the pinned tag determines both the `action.yaml`
orchestration and the matching Python code (the action installs from its own source at that
tag — there is no separate "latest package" that can drift).

To cut a new release:

1. **Merge your change into `main`** (PR reviewed, unit tests + E2E green).
2. **Bump the version** in [`setup.py`](setup.py) (`version="X.Y.Z"`) to the new semver.
   This is normally part of the same PR. CI **fails the release** if the git tag does not
   match this version.
3. **Tag and push:**

   ```bash
   git tag vX.Y.Z
   git push origin vX.Y.Z
   ```

   The [release workflow](.github/workflows/main.yaml) then: runs the tests → verifies the
   tag matches `setup.py` → builds the sdist → publishes a GitHub Release with auto-generated
   notes. Only plain semver tags (`vX.Y.Z`) trigger a release; pre-release tags do not.
4. **Bump the consumers.** In `kbc-stacks` (and any other repo using this action), update
   the pin to the new tag:

   ```yaml
   - uses: keboola/helm-image-updater@vX.Y.Z   # was @vA.B.C
   ```

   Consumers stay on their pinned version until you deliberately bump it — a HIU release does
   not change anyone's behavior until they move the pin.

> **Why the version must be bumped before tagging:** the package version lives in `setup.py`
> and the release job refuses to publish a tag whose number disagrees with it, so the GitHub
> Release, the git tag, and `pip show helm_image_updater` always agree.

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

### Testing a branch before release

The action installs its Python code from its **own checked-out source at the ref you pin** (`pip install "$GITHUB_ACTION_PATH"`). That means testing an unreleased branch needs no `action.yaml` surgery — just point a consumer at your branch:

```yaml
- uses: keboola/helm-image-updater@your-feature-branch
  with:
    helm-chart: metastore
    image-tag: canary-orion-metastore-0.0.5
    github-token: ${{ secrets.GITHUB_TOKEN }}
```

Both `action.yaml` and the Python code then come from `your-feature-branch`, in lockstep.

The preferred way to validate a branch is the **E2E suite** in [helm-image-updater-testing](https://github.com/keboola/helm-image-updater-testing/actions/workflows/test-suite.yaml) — trigger the "Test suite" workflow and pass your branch name. See [e2e tests](#e2e-tests) below.

## Testing

The test suite verifies the following functionality:

### Tag Update Strategies

- Development tag handling (`dev-*`)

  - Single stack updates
  - Auto-merged by HIU (non-production target)
  - Tag file modifications

- Production tag handling (`production-*`)

  - Promoter-managed rollout (unmerged PRs); never merged by HIU
  - Standard 2-wave / wave / manual-per-stack grouping
  - Concurrent stack updates

- Canary tag handling (`canary-*`)

  - Stack-specific updates
  - Auto-merged by HIU to the canary branch
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