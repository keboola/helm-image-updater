# HIU Legacy Deploy-Path Removal (ST-4159 folded into PR #43) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** HIU never merges anything to a production stack — release-promoter owns every production merge; dev/e2e/canary merges stay fast via HIU — by deleting all legacy deploy paths (`cloud_multi_stage`, `AUTOMERGE`, legacy no-strategy production grouping) from helm-image-updater and the legacy scenarios from helm-image-updater-testing.

**Architecture:** Extend existing draft PR keboola/helm-image-updater#43 (branch `tomaskacur-st-4169-tag-type-automerge`, worktree `/Users/tomaskacur/keboola/devel/helm-image-updater-st4169`, rebased on v0.22.0) with the ST-4159 removal commits → one release v0.23.0 closing ST-4169 + ST-4159. One new PR in `helm-image-updater-testing` (local checkout `/Users/tomaskacur/keboola/devel/helm-image-updater-testing`) replaces legacy scenarios with tag/stack-rule scenarios; the suite run (testing branch × HIU branch) is the release gate. A third, follow-up PR in kbc-stacks bumps the pin and strips the knob forwarding (workflow *inputs* stay — ~15 external dispatchers still send them).

**Tech Stack:** Python 3.13 + pytest (HIU), GitHub Actions matrix + `actions/github-script` JS (testing repo), GH workflows (kbc-stacks).

## Global Constraints

- Never remove the `automerge`/`multi-stage` **workflow inputs** from `kbc-stacks/.github/workflows/update-image-tag.yaml` — ~15 repos dispatch them directly; a removed input = 422, silently swallowed by the Azure dispatch scripts (deploy outage). Removing the *forwarding* (env/`with:` to HIU) is the goal; removing the declarations is a separate org-wide migration ticket.
- HIU env vars are NOT validated like workflow inputs — HIU simply not reading `AUTOMERGE`/`MULTI_STAGE` is safe regardless of what callers send.
- Composite-action inputs (HIU `action.yaml`) that a caller passes but the action no longer declares produce only a *warning*, not a failure. Old pins (`@v0.20.0`) are immutable and unaffected.
- End-state invariants (each must have a test):
  1. PRODUCTION/SEMVER-class deploy (any strategy, incl. empty) → PRs created **unmerged** (promoter- or human-merged). HIU has **no code path** that merges a PR whose stacks include a production stack.
  2. DEV/CANARY/INVALID-class deploy to non-production stacks (dev, e2e, canary, override) → auto-merged by HIU, fast.
  3. Non-production-class tag on a production override target → `validate()` error (already on the branch).
  4. Empty `DEPLOY_STRATEGY` ≡ `standard` (matches kbc-stacks' universal default since ST-4131/#20151); `OVERRIDE_STACK` runs stay single-PR regardless of strategy.
  5. Stray `MULTI_STAGE`/`AUTOMERGE` env from old dispatchers is ignored (warning for `MULTI_STAGE=true`); `DEPLOY_STRATEGY=cloud_multi_stage` is now an *invalid strategy* error.
- HIU tests run as: `cd /Users/tomaskacur/keboola/devel/helm-image-updater-st4169 && PYTHONPATH=$PWD /Users/tomaskacur/keboola/devel/helm-image-updater/.venv/bin/python -m pytest -q` (worktree shadows the editable install).
- Commits end with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`. PRs are drafts. Testing-repo branch must be cut from a fresh `origin/main`.

## Why this is safe now (context for reviewer)

- kbc-stacks `update-image-tag.yaml` (post-#20151, verified on origin/main) resolves `DEPLOY_STRATEGY=standard` for **every** non-override run with no promoter-* label; production rollout via promoter verified live (kai-agent #20164/#20165). The legacy no-strategy production path is **unreachable from kbc-stacks** except via `cloud_multi_stage` label mapping (dead — multi-stage users already migrated) and `OVERRIDE_STACK` runs (kept).
- `_should_auto_merge` on the branch already never merges to prod stacks; this plan removes the *other* legacy consumers of the knobs (grouping, titles) so the knobs can be deleted wholesale.

---

# Phase 1 — helm-image-updater (extend PR #43)

### Task 1: `environment.py` — delete AUTOMERGE/MULTI_STAGE parsing, cloud_multi_stage, promoter flags

**Files:**
- Modify: `helm_image_updater/environment.py`
- Test: `tests/test_legacy_knobs_removed.py` (new)

**Interfaces:**
- Produces: `EnvironmentConfig` WITHOUT fields `automerge`, `multi_stage`, `promoter_managed_standard`, `promoter_managed_manual_per_stack`. `deploy_strategy: DeployStrategy = DeployStrategy.STANDARD` (empty env → STANDARD, now meaning promoter-standard). Everything else unchanged.

- [ ] **Step 1: Write the failing tests** (`tests/test_legacy_knobs_removed.py`):

```python
"""ST-4159: legacy knobs are gone — AUTOMERGE/MULTI_STAGE env ignored,
cloud_multi_stage invalid, empty strategy resolves to standard."""
import pytest
from helm_image_updater.environment import EnvironmentConfig
from helm_image_updater.models import DeployStrategy

BASE = {"HELM_CHART": "x", "IMAGE_TAG": "production-abc",
        "GH_TOKEN": "t", "GH_APPROVE_TOKEN": "a"}


def test_automerge_env_not_parsed():
    cfg = EnvironmentConfig.from_env({**BASE, "AUTOMERGE": "false"})
    assert not hasattr(cfg, "automerge")


def test_multi_stage_env_ignored_with_warning(capsys):
    cfg = EnvironmentConfig.from_env({**BASE, "MULTI_STAGE": "true"})
    assert not hasattr(cfg, "multi_stage")
    assert cfg.deploy_strategy == DeployStrategy.STANDARD
    assert "MULTI_STAGE is deprecated" in capsys.readouterr().out


def test_empty_strategy_is_standard():
    cfg = EnvironmentConfig.from_env(BASE)
    assert cfg.deploy_strategy == DeployStrategy.STANDARD
    assert not hasattr(cfg, "promoter_managed_standard")


def test_cloud_multi_stage_is_invalid_strategy():
    cfg = EnvironmentConfig.from_env({**BASE, "DEPLOY_STRATEGY": "cloud_multi_stage"})
    assert any("Invalid DEPLOY_STRATEGY" in e for e in cfg.validate())
```

- [ ] **Step 2: Run to verify failure** — `pytest tests/test_legacy_knobs_removed.py -q` → FAIL (`automerge` attr exists, etc.)

- [ ] **Step 3: Implement.** In `environment.py`:
  - Delete dataclass fields + docstrings: `automerge` (l.21), `multi_stage` (l.23), `promoter_managed_standard` (l.24-29), `promoter_managed_manual_per_stack` (l.30-32) — the last two have **zero** consumers after Task 3.
  - Replace the parse block (l.75-114) with:

```python
        # Parse DEPLOY_STRATEGY. Empty -> standard, the universal default (ST-4131/ST-4159):
        # a PRODUCTION deploy is ALWAYS promoter-managed; there is no legacy fallback.
        # AUTOMERGE is dead (auto-merge is decided by tag class + target stacks, ST-4169)
        # and MULTI_STAGE/cloud_multi_stage was removed (ST-4159) -- old dispatchers still
        # send both, so tolerate-and-ignore (warn for MULTI_STAGE so stragglers surface).
        raw_strategy = env.get("DEPLOY_STRATEGY", "").strip().lower()
        deploy_strategy = DeployStrategy.STANDARD
        deploy_strategy_error = None
        if raw_strategy:
            try:
                deploy_strategy = DeployStrategy(raw_strategy)
            except ValueError:
                deploy_strategy_error = (
                    f"Invalid DEPLOY_STRATEGY: '{raw_strategy}'. "
                    "Must be one of: standard, gradual, critical, "
                    "critical-manual-gate, manual-per-stack"
                )
        if env.get("MULTI_STAGE", "false").lower() == "true":
            print(
                "WARNING: MULTI_STAGE is deprecated and ignored "
                "(cloud_multi_stage was removed in ST-4159; production deploys are promoter-managed)"
            )
```

  - Remove `automerge=…`, `multi_stage=…`, `promoter_managed_standard=…`, `promoter_managed_manual_per_stack=…` from the `cls(...)` call. Keep everything else EXPLICITLY, in particular `config._deploy_strategy_error = deploy_strategy_error` after construction — the `cloud_multi_stage`-is-invalid test depends on it (codex review).

- [ ] **Step 4: Run** `pytest tests/test_legacy_knobs_removed.py -q` → 4 PASS. (Full suite is red until Task 6 — expected.)

- [ ] **Step 5: Commit** `refactor(ST-4159): drop AUTOMERGE/MULTI_STAGE knobs and cloud_multi_stage from env config`

### Task 2: `models.py` — remove `CLOUD_MULTI_STAGE` + `UpdatePlan.multi_stage`

**Files:**
- Modify: `helm_image_updater/models.py`

**Interfaces:**
- Produces: `DeployStrategy` = {STANDARD, GRADUAL, CRITICAL, CRITICAL_MANUAL_GATE, MANUAL_PER_STACK}; `is_wave` unchanged; `is_promoter_managed` now returns True for every member (keep the property — the E2E/kbc-stacks docs reference it — but rewrite its docstring: "every strategy is promoter-capable; DEV/CANARY/OVERRIDE runs are exempted at the plan level (`plan.strategy`), not the strategy level"). `UpdatePlan` loses `multi_stage`.

- [ ] **Step 1:** Delete `CLOUD_MULTI_STAGE = "cloud_multi_stage"` (l.21); delete `UpdatePlan.multi_stage: bool = False` (l.110); rewrite the two docstrings (the `is_promoter_managed` one currently documents the legacy-default caveat — now stale).
- [ ] **Step 2:** `pytest tests/test_models.py -q` → note failures (fixed in Task 6).
- [ ] **Step 3: Commit** `refactor(ST-4159): remove cloud_multi_stage strategy and UpdatePlan.multi_stage`

### Task 3: `plan_builder.py` — one production path: promoter-managed, always

**Files:**
- Modify: `helm_image_updater/plan_builder.py`

**Interfaces:**
- Consumes: Task 1's `EnvironmentConfig` (no knob fields).
- Produces: `_is_promoter_managed_standard(config, plan)` keyed on enum only; `_group_changes_for_prs` with NO multi_stage branch and NO automerge grouping; `_create_pr_plan` without `multi_stage_*` pr_types.

- [ ] **Step 1:** Replace `_is_promoter_managed_standard` (l.33-44) body/docstring:

```python
def _is_promoter_managed_standard(config: EnvironmentConfig, plan: UpdatePlan) -> bool:
    """True iff this run is the promoter-managed `standard` 2-wave release (ST-4126):
    DEPLOY_STRATEGY resolves to standard -- which since ST-4159 includes the EMPTY
    default -- AND a PRODUCTION deploy. ONLY production is staged: DEV, CANARY and
    OVERRIDE are orthogonal UpdateStrategy axes that keep their own single-PR handling
    and are never promoter-managed (a dev push stays a fast auto-merged deploy)."""
    return (
        config.deploy_strategy == DeployStrategy.STANDARD
        and plan.strategy == UpdateStrategy.PRODUCTION
    )
```

- [ ] **Step 2:** In `prepare_plan` remove `multi_stage=config.multi_stage,` from the `UpdatePlan(...)` construction (l.90).
- [ ] **Step 3:** In `_group_changes_for_prs`, delete the whole multi-stage branch (l.500-541) and the legacy tail (l.543-572); replace the tail with:

```python
    # PRODUCTION is unreachable here by construction (standard 2-wave is the ST-4159
    # default; wave / manual-per-stack are explicit) -- guard so no future regression
    # can ever route a production deploy into an auto-mergeable single PR.
    if plan.strategy == UpdateStrategy.PRODUCTION:
        raise RuntimeError(
            "PRODUCTION deploys must be promoter-managed "
            "(standard/gradual/critical/critical-manual-gate/manual-per-stack); "
            "the legacy grouping was removed in ST-4159."
        )

    # DEV / OVERRIDE (and any defensive single-change case): one auto-mergeable PR.
    return [{
        'stacks': [sc['stack'] for sc in stack_changes],
        'changes': stack_changes,
        'base_branch': 'main',
        'pr_type': 'standard'
    }]
```

- [ ] **Step 4:** In `_create_pr_plan`: delete the `pr_type.startswith('multi_stage_')` branch-name branch (l.728-732); in the final `else` title branch call:

```python
        pr_title_prefix = generate_pr_title_prefix(
            strategy=plan.strategy,
            target_stacks=pr_group['stacks'],
        )
```

  (drop `is_multi_stage`, `user_requested_automerge`, `cloud_provider` args). Also grep the file for any remaining `plan.multi_stage` / `config.automerge` / `cloud_provider` and delete leftovers (l.776-777 are inside this call; `cloud_provider` local at l.726 becomes unused — remove).
- [ ] **Step 5:** `grep -n 'multi_stage\|automerge\|cloud_multi' helm_image_updater/plan_builder.py` → expect ONLY the `_should_auto_merge` docstring mentions (fine) — no live code refs.
- [ ] **Step 6: Commit** `refactor(ST-4159): single production path — promoter-managed grouping only`

### Task 4: `message_generation.py` — simplify PR title prefix

**Files:**
- Modify: `helm_image_updater/message_generation.py:59-107`

**Interfaces:**
- Produces: `generate_pr_title_prefix(strategy: UpdateStrategy, target_stacks: List[str]) -> str` → `[canary sync]` | `[test sync]` | `[prod sync]` (multi-stage prefixes gone). Sole caller updated in Task 3.

- [ ] **Step 1:** Replace the function:

```python
def generate_pr_title_prefix(
    strategy: UpdateStrategy,
    target_stacks: List[str],
) -> str:
    """[canary sync] for canary; [test sync] when every target stack is a dev stack;
    [prod sync] otherwise (incl. e2e/override targets, matching historical titles)."""
    if strategy == UpdateStrategy.CANARY:
        return "[canary sync]"
    is_dev_update = bool(target_stacks) and all(
        classify_stack(stack).is_dev for stack in target_stacks
    )
    return "[test sync]" if is_dev_update else "[prod sync]"
```

- [ ] **Step 2:** Grep `message_generation.py` for other `multi_stage`/`automerge` refs (PR body text at ~l.313 mentions overrides — unrelated, keep) and remove any leftovers.
- [ ] **Step 3: Commit** `refactor(ST-4159): drop multi-stage PR title prefixes`

### Task 5: delete `UpdateConfig`; clean `cli.py`

**Files:**
- Modify: `helm_image_updater/config.py` (delete class l.45-58 + docstring ref l.17), `helm_image_updater/cli.py` (delete prints l.43 `Automerge:` and l.45 `Multi-stage deployment:`)

`UpdateConfig` has **zero** source consumers (verified) — tests referencing it get fixed/deleted in Task 6.

- [ ] **Step 1:** Make both edits; `grep -rn 'UpdateConfig' helm_image_updater/` → no hits.
- [ ] **Step 2: Commit** `refactor(ST-4159): delete dead UpdateConfig; drop legacy CLI prints`

### Task 6: test sweep — delete legacy tests, port the rest, add invariants

**Files:**
- Modify/Delete tests across: `test_core.py`, `test_cli_functional.py`, `test_deploy_strategy.py`, `test_standard_2wave.py`, `test_plan_builder.py`, `test_wave_grouping.py`, `test_manual_per_stack.py`, `test_models.py`, `test_auto_merge.py`
- Create: (already) `tests/test_legacy_knobs_removed.py`

Rules for the sweep (run `pytest -q`, fix every failure by exactly one of):
1. **Delete** tests whose *subject* was removed: cloud_multi_stage grouping/strategy tests, MULTI_STAGE-alias parsing tests, AUTOMERGE parsing tests, `promoter_managed_standard`-flag tests, multi-stage 6-PR grouping tests, legacy "production single combined PR (automerge=true)" / "per-stack PRs (automerge=false)" grouping tests, `UpdateConfig` construction tests, the `@pytest.mark.skip` `test_multi_cloud_multi_stage_automerge_true`, and multi-stage title-prefix tests.
2. **Port** tests whose subject survives: remove `automerge=`/`multi_stage=`/`promoter_managed_standard=` kwargs from `EnvironmentConfig(...)`/Mock constructions; update `generate_pr_title_prefix` call sites to the 2-arg signature; update `UpdatePlan(...)` constructions dropping `multi_stage=`.
3. **CLI-output assertions** (codex review): `tests/test_cli_functional.py` asserts the deleted stdout lines (`"Automerge: False"`, `"Multi-stage deployment: True"`, ~l.189) — delete those specific assertions (or the whole test when the print was its only subject); production-run functional tests get re-pointed at the 2-wave outcome per rule 4.
4. **Repurpose** the legacy happy-path: production tag + EMPTY strategy must now produce the 2-wave promoter release. Add to `test_standard_2wave.py`:

```python
def test_empty_strategy_production_is_promoter_standard(monkeypatch):
    """ST-4159: the empty-strategy default IS promoter standard -- a production tag
    with no DEPLOY_STRATEGY produces the 2-wave dev->prod release, PRs unmerged."""
    cfg = EnvironmentConfig.from_env({
        "HELM_CHART": "dummy-service", "IMAGE_TAG": "production-abc",
        "GH_TOKEN": "t", "GH_APPROVE_TOKEN": "a",
    })
    assert cfg.deploy_strategy == DeployStrategy.STANDARD
    plan = Mock()
    plan.strategy = UpdateStrategy.PRODUCTION
    assert _is_promoter_managed_standard(cfg, plan) is True
```

   and its override counterpart:

```python
def test_override_stack_never_promoter_standard():
    cfg = EnvironmentConfig.from_env({
        "HELM_CHART": "dummy-service", "IMAGE_TAG": "production-abc",
        "OVERRIDE_STACK": "kbc-us-east-1",
        "GH_TOKEN": "t", "GH_APPROVE_TOKEN": "a",
    })
    plan = Mock()
    plan.strategy = UpdateStrategy.OVERRIDE  # _determine_strategy: override wins first
    assert _is_promoter_managed_standard(cfg, plan) is False
```

- [ ] **Step 1:** Run full suite; apply rules 1-3 file by file; re-run until green.
- [ ] **Step 2:** Grep tests for stragglers: `grep -rn 'automerge\|multi_stage\|MULTI_STAGE\|AUTOMERGE\|cloud_multi_stage\|UpdateConfig\|promoter_managed' tests/` → remaining hits must be only (a) `test_legacy_knobs_removed.py`, (b) `_should_auto_merge` tests (the function name contains "auto_merge" — fine), (c) the MULTI_STAGE deprecation-warning test.
- [ ] **Step 3:** Full suite green. Record the count.
- [ ] **Step 4: Commit** `test(ST-4159): drop legacy deploy-path tests, add promoter-default invariants`

### Task 7: `action.yaml` + README + version 0.23.0

**Files:**
- Modify: `action.yaml` (delete `automerge:` input l.10-13, `multi-stage:` input l.18-21, and their env-mapping lines `AUTOMERGE:`/`MULTI_STAGE:` in the `runs:` block), `README.md` (rewrite strategy/inputs docs: no automerge/multi-stage/cloud_multi_stage; document "production ⇒ promoter-managed, always; empty strategy = standard; merge decided by tag class + target stacks"), `CLAUDE.md` (drops its `AUTOMERGE`/`MULTI_STAGE` env-var documentation — codex review), `setup.py` (`version="0.22.0"` → `"0.23.0"`).

Note: callers still passing the deleted action inputs get a GH *warning*, not an error; the only live caller at HEAD is kbc-stacks (updated in Task 15) — old pins are unaffected.

- [ ] **Step 1:** Make the three edits; `grep -n 'AUTOMERGE\|MULTI_STAGE\|automerge\|multi-stage\|cloud_multi_stage' action.yaml README.md` → no hits.
- [ ] **Step 2:** Full suite still green.
- [ ] **Step 3: Commit** `chore(ST-4159): drop knob inputs from action.yaml, update README, bump to 0.23.0`

### Task 8: push + retitle PR #43

- [ ] Push branch; update PR #43 title to `ST-4169 + ST-4159: tag/stack-based auto-merge + legacy deploy-path removal` and body (Changes += Tasks 1-7; "closes ST-4159" Linear link; release = v0.23.0; keep the do-not-merge gate = E2E). Reply on the PR threads only if new review comments appeared.

---

# Phase 2 — helm-image-updater-testing PR

Branch: `tomaskacur-st-4159-drop-legacy-scenarios` cut from fresh `origin/main`.

### Task 9: drop legacy scenarios

**Files:**
- Modify: `.github/workflows/test-suite.yaml`

- [ ] **Step 1:** Delete matrix entries: `happy-path` (l.78-105), `multi-stage` (l.107-145), `dont-merge` (l.147-180), `multi-stage-extra-tags` (l.182-226), `multi-stage-one-cloud` (l.345-383). (`non-existent-service` stays; `override-stack` stays as-is — its `[prod sync] … in dev-keboola-gcp-us-east1-e2e` + `merged: true` assertions remain correct under the tag/stack rule.)
- [ ] **Step 2:** Update the `summary` job's hardcoded list (l.1058-1066) to the new scenario set (all names from Tasks 9-11).
- [ ] **Step 3 (codex review):** Tighten `run-assertions.js` merge semantics — today `merged: false` passes when *not all* matching PRs are merged, so 1-merged-of-2 slips through; our new unmerged-wave assertions need "NONE merged". In BOTH `pr_created` (l.102-108) and `pr_created_group` (l.181-187) replace the `actualMerged` comparison:

```js
          if (assertion.merged !== undefined && matchingPrs.length > 0) {
            const mergedPrs = matchingPrs.filter(pr => pr.merged_at !== null);
            // merged: true -> ALL matching PRs merged; merged: false -> NONE merged.
            // (Previously false meant "not all merged", letting a partial merge pass.)
            mergeMatch = assertion.merged
              ? mergedPrs.length === matchingPrs.length
              : mergedPrs.length === 0;
            mergeDetails = ` Merge status: ${mergedPrs.length}/${matchingPrs.length} merged (expected: ${assertion.merged ? 'all merged' : 'none merged'}).`;
          }
```

  (mirror the same change with the `Group`-suffixed variables in `pr_created_group`). Safe for the surviving scenarios: after Task 9 the only `merged:` consumers are this PR's scenarios and `override-stack`/`non-existent-service` (merged:true / count:0).
- [ ] **Step 4: Commit** `test(ST-4159): drop legacy direct-automerge scenarios; strict none-merged assertion semantics`

### Task 10: rework `override-removal` + `override-removal-with-values` for the 2-wave world

These two keep e2e coverage of the `appManifestsRevision` removal feature, which now ships inside the standard release's **wave 1** (prod) PR. Wave titles are `[dummy-service standard wave N] dummy-service@{image_tag}`. dummy-service spans 3 dev + 6 prod stacks → wave 0 = 3 files, wave 1 = 6 tag.yaml (+1 values.yaml) = 7.

- [ ] **Step 1:** Replace both scenarios' `assertions` (keep `inputs` but they now carry `deploy-strategy: ""` and the `automerge: true` value becomes explicitly-ignored documentation; keep `setup_files` unchanged). New assertions for `override-removal` (same shape for `-with-values`, which additionally keeps its `content_contains` check):

```yaml
            assertions:
              - type: "workflow_success"
                description: "Workflow completes successfully"
              - type: "pr_created_group"
                group_name: "waves"
                count: 2
                merged: false
                title_pattern_regex: "\\[dummy-service standard wave [01]\\] dummy-service@{image_tag}"
                description: "2 unmerged wave PRs (empty strategy = promoter standard)"
              - type: "pr_created"
                count: 1
                merged: false
                title_pattern_regex: "\\[dummy-service standard wave 1\\] dummy-service@{image_tag}"
                description: "wave 1 (prod) PR — scope file assertions to it"
              - type: "files_modified"
                pattern: "*/dummy-service/*"
                expected_count: 7
                description: "wave 1: 6 prod tag.yaml + 1 values.yaml override removal"
              - type: "yaml_content"
                files: "*/dummy-service/tag.yaml"
                path: "image.tag"
                expected_value: "{image_tag}"
                description: "prod tag.yaml files carry the new tag"
              - type: "file_content_check"
                files: "com-keboola-gcp-europe-west3/dummy-service/values.yaml"
                check: "override_removed"
                description: "values.yaml no longer contains argocdApplication.appManifestsRevision"
```

  (Ordering matters: the scoped `pr_created` runs *after* the group assert so `foundTestPrs` = wave 1 only — `file_content_check` fails on any foundTestPr lacking the file.)
- [ ] **Step 2 (placement — codex review):** These scenarios leave 2 OPEN wave PRs each, including a `release:wave:0` anchor with a manifest — a live release for dummy-service. Any LATER promoter-drive scenario on the same app would discover it and FIFO-block. Therefore both scenarios go at the **END of the matrix, after every `promoter_drive` scenario** (order: …drives… → `override-removal` → `override-removal-with-values` → `non-existent-service`). `prod-override-unmerged` (label-free) stays early for the noop check. The next suite run's `cleanup.js` closes the leftover PRs + strips labels — no changes needed there.
- [ ] **Step 3: Commit** `test(ST-4159): override-removal scenarios assert the wave-1 (prod) PR`

### Task 11: add the tag/stack-rule scenarios

**Files:**
- Modify: `.github/workflows/test-suite.yaml` (matrix)

- [ ] **Step 1:** Add FOUR scenarios (full YAML; `automerge`/`multi-stage` inputs deliberately set to values HIU must ignore):

```yaml
          - name: "default-standard-rollout"
            description: "EMPTY deploy-strategy = promoter standard (ST-4159): production tag + extra tag, automerge=true IGNORED -> 2 unmerged wave PRs; promoter merges dev -> sync -> prod -> complete"
            promoter_drive: true
            promoter_drive_standard: true
            promoter_expected_waves: "0,1"
            promoter_with_uat: "false"
            inputs:
              helm-chart: "dummy-service"
              image-tag-prefix: "production-default-standard"
              automerge: true
              multi-stage: false
              dry-run: false
              extra-tag1: "agent.tag:{agent_tag}"
              extra-tag2: ""
              override-stack: ""
              deploy-strategy: ""
            assertions:
              - type: "workflow_success"
                description: "Workflow completes successfully"
              - type: "pr_created"
                count: 2
                merged: false
                title_pattern_regex: "\\[dummy-service standard wave [01]\\] dummy-service@{image_tag} agent\\.tag@{agent_tag}"
                description: "2 unmerged wave PRs carrying image + extra tag (HIU merged nothing)"
              - type: "files_modified"
                pattern: "*/dummy-service/tag.yaml"
                expected_count: 9
                description: "3 dev (wave 0) + 6 prod (wave 1) tag.yaml"
              - type: "yaml_content"
                files: "*/dummy-service/tag.yaml"
                path: "image.tag"
                expected_value: "{image_tag}"
                description: "image.tag updated in both waves"
              - type: "yaml_content"
                files: "*/dummy-service/tag.yaml"
                path: "agent.tag"
                expected_value: "{agent_tag}"
                description: "extra tag updated in both waves"
              # promoter-drive-standard.js then drives dev->prod to promoter:complete
              # (STRATEGY is hardcoded 'standard' in that harness; empty input is fine).

          - name: "dev-tag-fast-path"
            description: "dev- tag -> single [test sync] PR to the 3 central dev stacks, auto-merged by HIU (tag/stack rule; no promoter)"
            inputs:
              helm-chart: "dummy-service"
              image-tag-prefix: "dev-fast-path"
              automerge: false
              multi-stage: false
              dry-run: false
              extra-tag1: ""
              extra-tag2: ""
              override-stack: ""
              deploy-strategy: ""
            assertions:
              - type: "workflow_success"
                description: "Workflow completes successfully"
              - type: "pr_created"
                count: 1
                merged: true
                title_pattern_regex: "\\[test sync\\] dummy-service@{image_tag}"
                description: "1 PR auto-merged even with automerge=false input (input is dead)"
              - type: "files_modified"
                pattern: "*/dummy-service/tag.yaml"
                expected_count: 3
                description: "exactly the 3 dev stacks"
              - type: "yaml_content"
                files: "*/dummy-service/tag.yaml"
                path: "image.tag"
                expected_value: "{image_tag}"
                description: "dev tag.yaml files updated"

          - name: "pr-test-override-dev"
            description: "pr-test-* (INVALID-class) tag + override-stack=dev central -> merged single-stack PR (the connection PR-test flow)"
            inputs:
              helm-chart: "dummy-service"
              image-tag-prefix: "pr-test-7786"
              automerge: true
              multi-stage: false
              dry-run: false
              extra-tag1: ""
              extra-tag2: ""
              override-stack: "dev-keboola-gcp-us-central1"
              deploy-strategy: ""
            assertions:
              - type: "workflow_success"
                description: "Workflow completes successfully"
              - type: "pr_created"
                count: 1
                merged: true
                title_pattern_regex: "\\[test sync\\] dummy-service@{image_tag} in dev-keboola-gcp-us-central1"
                description: "1 merged PR targeting only the override stack"
              - type: "files_modified"
                pattern: "*/dummy-service/tag.yaml"
                expected_count: 1
                description: "only the override stack's tag.yaml"

          - name: "prod-override-unmerged"
            description: "production tag + override-stack=<prod stack> -> single PR created UNMERGED (HIU never merges to a production stack); promoter no-op guard runs here (0 releases)"
            promoter_noop_check: true
            inputs:
              helm-chart: "dummy-service"
              image-tag-prefix: "production-prod-override"
              automerge: true
              multi-stage: false
              dry-run: false
              extra-tag1: ""
              extra-tag2: ""
              override-stack: "kbc-us-east-1"
              deploy-strategy: ""
            assertions:
              - type: "workflow_success"
                description: "Workflow completes successfully"
              - type: "pr_created"
                count: 1
                merged: false
                title_pattern_regex: "\\[prod sync\\] dummy-service@{image_tag} in kbc-us-east-1"
                description: "1 PR, NOT merged despite automerge=true (prod stack in targets)"
              - type: "files_modified"
                pattern: "*/dummy-service/tag.yaml"
                expected_count: 1
                description: "only kbc-us-east-1 tag.yaml"
```

- [ ] **Step 2:** Matrix placement/order: `prod-override-unmerged` takes `dont-merge`'s old slot (before any wave-PR-producing scenario — the noop check must observe `0 release(s)` with no open wave anchors); `default-standard-rollout` goes next to `standard-rollout` (it completes its release, so no leftovers); `dev-tag-fast-path`/`pr-test-override-dev` anywhere before the drives; `override-removal`/`-with-values` LAST (Task 10 Step 2 — they leave open wave anchors).
- [ ] **Step 3:** Note in the matrix comment why canary has no e2e scenario: the testing repo has no `canary-orion` branch (HIU's canary flow switches to it); canary auto-merge is unit-tested (`test_standard_2wave.py` canary cases + `test_auto_merge.py`).
- [ ] **Step 4: Commit** `test(ST-4159): add tag/stack-rule scenarios (default-standard, dev fast path, pr-test override, prod-override unmerged)`

### Task 12: wrapper + docs

**Files:**
- Modify: `.github/workflows/update-image-tag.yaml` (testing repo wrapper): keep the `automerge`/`multi-stage` inputs AND keep forwarding `AUTOMERGE`/`MULTI_STAGE` env to HIU — that *is* the back-compat test (HIU must ignore them). Update only the `deploy-strategy` input descriptions (2×, l.46+l.103): `"Release rollout strategy: standard | gradual | critical | critical-manual-gate | manual-per-stack (empty = standard)"`, and reword the knob descriptions to `"DEAD KNOB (ST-4159): forwarded to HIU, which ignores it — kept to prove back-compat"`.
- Modify: `README.md` + `CLAUDE.md`: replace the scenario lists with the new set; state the promoter-owns-production model; drop multi-stage/automerge descriptions.

- [ ] **Step 1:** Make edits.
- [ ] **Step 2: Commit** `docs(ST-4159): document promoter-owned production + dead knobs in the harness`

### Task 13: draft PR + release-gate run

- [ ] **Step 1:** Push branch; open **draft** PR (title `ST-4159: drop legacy scenarios, add tag/stack-rule scenarios`, link ST-4159, note it must merge together with helm-image-updater#43 / v0.23.0).
- [ ] **Step 2:** Dispatch the suite: `gh workflow run test-suite.yaml --repo keboola/helm-image-updater-testing --ref tomaskacur-st-4159-drop-legacy-scenarios -f helm-image-updater-branch=tomaskacur-st-4169-tag-type-automerge` → poll to completion.
- [ ] **Step 3:** All jobs green = **release gate satisfied**. Fix + re-run otherwise.

---

# Phase 3 — release + kbc-stacks follow-up

### Task 14: release v0.23.0

- [ ] Merge HIU PR #43 (squash per repo convention — check `gh pr list --state merged -L3` for the pattern first); tag `v0.23.0` on the merge commit; merge the testing PR; re-dispatch the suite `main` × `main` as confirmation.

### Task 15: kbc-stacks PR (pin + stop forwarding knobs)

**Files:**
- Modify: `.github/workflows/update-image-tag.yaml`:
  1. Pin `keboola/helm-image-updater@v0.20.0` → `@v0.23.0`.
  2. Delete `automerge:` + `multi-stage:` from the action's `with:` block (the action no longer declares them).
  3. Delete the `promoter-cloud-multi-stage` label mapping (elif at l.318-319 area) and the `cloud_multi_stage` routing branch; a stray label now falls through to the universal standard default.
  4. Simplify the routing block: `SHOULD_AUTOMERGE`/`SHOULD_MULTI_STAGE` outputs disappear entirely (nothing consumes them); keep resolving + logging `DEPLOY_STRATEGY` only. The e2e-override branch keeps clearing `DEPLOY_STRATEGY`.
  5. **Keep the workflow `automerge`/`multi-stage` input declarations** with descriptions updated to `"DEPRECATED, ignored (ST-4159) — kept so existing dispatchers don't 422"`.
- Modify: `README.md` deploy-strategy section (drop cloud_multi_stage + automerge semantics; production always promoter-managed).
- Do NOT touch `.github/actions/trigger-image-tag-update/action.yaml` (it dispatches the workflow, whose inputs stay).

- [ ] Draft PR named after ST-4159, from a fresh-fetched `origin/main` branch; template; link issue. **Re-verify the routing block on the fresh origin/main first** — the local worktree copy is stale (still shows `STANDARD_DEFAULT_APPS`); the line anchors above are from `git show origin/main:...` as of 2026-07-13 and kbc-stacks main moves ~100 commits/day (codex review).

### Task 16: post-release verification

- [ ] Watch the first few real deploys after the pin merge (any `production-*` app): expect 2 unmerged wave PRs + promoter merges; an e2e/`pr-test` override deploy: expect fast auto-merge. `gh pr list --repo keboola/kbc-stacks --search "wave in:title" -L 6` + spot-check one app's release to `promoter:complete`. Update the memory file + Linear (close ST-4169 + ST-4159).

---

# Codex review (gpt-5.5) — outcome

Two passes (design pass against code; task-level pass against the plan doc + all three repos): **APPROVE-WITH-CHANGES**, all applied above:
1. Keep the `_deploy_strategy_error` assignment explicit in Task 1 (invalid-strategy test depends on it).
2. Test-sweep rule for CLI stdout assertions (`test_cli_functional.py` ~l.189) — added as rule 3.
3. `merged: false` in run-assertions.js meant "not all merged" — tightened to "none merged" (Task 9 Step 3).
4. `override-removal` scenarios leave a live open `release:wave:0` anchor → moved to END of matrix so they can't FIFO-block later same-app promoter drives (Task 10 Step 2).
5. Defensive `PRODUCTION ⇒ raise` guard before the grouping tail (Task 3).
6. HIU `CLAUDE.md` also documents the dead knobs → added to Task 7; wrapper line anchors corrected (l.46/103); Task 15 re-verify note (stale local kbc-stacks checkout).
Confirmed correct as planned: empty-strategy pivot (override precedence, e2e-override clearing, wrapper), grouping-tail claim, validate() behavior for cloud_multi_stage/MULTI_STAGE, wave title regexes + file counts (3 / 6+1), foundTestPrs scoping order, noop-check relocation shape, action-input removal (warning-only), SHOULD_AUTOMERGE/SHOULD_MULTI_STAGE outputs have no consumers beyond the HIU `with:` block, release sequencing (prod pinned to v0.20.0 until the kbc-stacks PR).

# Self-review notes / risks

1. **Empty-strategy semantics change is the biggest behavioral pivot**: any *non-kbc-stacks* caller sending a production tag with no strategy now gets 2 unmerged wave PRs instead of a merged single PR. Known callers: kbc-stacks (sends `standard` explicitly — unaffected), the testing harness (updated here), kbc-stacks-playground (test sandbox, pinned to an old tag — unaffected until it bumps).
2. **`promoter_noop_check` relocation**: `dont-merge` (9 unmerged PRs) → `prod-override-unmerged` (1 unmerged PR). Weaker corpus but same property ("promoter observes 0 releases, merges nothing"); the wave-drive scenarios cover the promoter's positive paths.
3. **Extra-tags e2e coverage** previously lived in `multi-stage-extra-tags`; it moves into `default-standard-rollout` (extra tag asserted in both wave PRs + driven to complete).
4. **Order sensitivity in run-assertions.js**: `pr_created` overwrites `foundTestPrs`; Task 10/11 assertions are ordered so file checks bind to the intended PR set.
5. **`[prod sync]` prefix retained for e2e/override targets** (e2e stacks are not `is_dev`) — existing `override-stack` scenario regex stays valid; historical title format preserved.
6. Wave-1 file count (7) depends on the 6-prod-stack fixture set; if a stack dir is added later the assertion needs a bump (same maintenance property as the old `expected_count: 9`).
7. kbc-stacks Task 15 must NOT break the e2e-override dispatch path used by e2e pipelines (inputs preserved; only forwarding removed).
