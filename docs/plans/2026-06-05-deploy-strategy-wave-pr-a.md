# PR-A: DEPLOY_STRATEGY & wave PRs (helm-image-updater) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Teach helm-image-updater to emit promoter-consumable **wave PRs** (created up front, unmerged, labeled `release:id` / `release:wave:N` / `deploy:<strategy>`) behind a new `DEPLOY_STRATEGY` knob, without breaking existing deploys.

**Architecture:** A single `DEPLOY_STRATEGY` enum drives an internal grouping dispatch (inspired by, and superseding, PR #19): `standard` (single PR) / `cloud_multi_stage` (today's multi-stage) / wave strategies (`gradual|critical|critical-manual-gate` → one PR per rollout wave). Auto-merge is decoupled (wave PRs never auto-merge — promoter owns the merges). Wave membership comes from a per-stack `rollout_wave` in `stack-metadata.yaml` (default: dev→0, else→3). `release:id` is a length-bounded, deterministic slug. Re-runs are idempotent (search by `release:id`).

**Tech Stack:** Python 3.13 · pytest · ruamel.yaml · PyGithub · GitPython.

**Spec:** `helm-image-updater/deploy-strategy-promoter-integration.md`. Promoter contract: `release-promoter/DESIGN.md` §2–§5.

**Conventions for every task:** run tests with `python -m pytest <path> -v`. Commit messages end with the trailer `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`. Work on a new branch off `main` (see Task 0). One logical step per commit. **Do not auto-merge or push** unless the user asks.

---

## File Structure

- **Modify** `helm_image_updater/models.py` — add `DeployStrategy` enum; add `PRPlan.labels`.
- **Modify** `helm_image_updater/environment.py` — parse + validate `DEPLOY_STRATEGY`.
- **Create** `helm_image_updater/wave_planning.py` — pure helpers: `compute_release_id`, `resolve_wave`, label builders, wave-grouping.
- **Modify** `helm_image_updater/plan_builder.py` — dispatch grouping by strategy; standard single-PR; `_create_pr_plan` wave branch/title/labels; idempotency guard in `prepare_plan`.
- **Modify** `helm_image_updater/plan_executor.py` — thread `labels` to io_layer; dry-run prints labels.
- **Modify** `helm_image_updater/io_layer.py` — `create_branch_commit_and_pr`/`create_pull_request` accept + provision + apply labels; `find_prs_by_label`.
- **Modify** `action.yaml` — add `deploy-strategy` input → `DEPLOY_STRATEGY` env.
- **Create** `tests/test_deploy_strategy.py` — parsing/validation/release_id/resolve_wave (pure).
- **Create** `tests/test_wave_grouping.py` — wave grouping, auto-merge, labels, idempotency.
- **Modify** `tests/test_plan_builder.py` — add the `standard` + `automerge=false` single-PR test.

---

## Model & review gates (per task)

The orchestrator dispatches each task to a fresh subagent at the **model** below, does a two-stage
review of its output, then runs the **Codex gate** where marked before moving on. Codex runs via
`mcp__codex-cli__review` (or a `codex` prompt) with **model `gpt-5.5`** — the `*-codex` model ids are
rejected on this ChatGPT account — against the task's commit diff (`base: main` or `commit: <sha>`).
**Codex findings are claims to verify against the code before acting** (project CLAUDE.md), never applied blind.

| Task | Model | Codex | Why |
|------|-------|-------|-----|
| 0 Branch | — | no | mechanical git |
| 1 DeployStrategy enum + parsing + alias | **sonnet** | **yes** | public-interface contract; the `multi_stage` sync is load-bearing |
| 2 Validation (unknown→error, wave needs prod) | sonnet | no | straightforward rules, fully unit-tested |
| 3 `PRPlan.labels` field | **haiku** | no | one-line dataclass field |
| 4 `compute_release_id` | sonnet | **yes** | 50-char label limit + collision-resistance (the bug class Codex first flagged) |
| 5 `resolve_wave` | sonnet | no | small pure fn, table-tested |
| 6 Wave grouping dispatch | **opus** | **yes** | heart of PR-A: labels, contiguity guard, dispatch ordering vs canary/multi-stage |
| 7 standard automerge=false → 1 PR | sonnet | **yes** | changes a production deploy path — confirm nothing relied on per-stack |
| 8 `_should_auto_merge` wave→never | **haiku** | no | one branch, unit-tested |
| 9 `_create_pr_plan` wave branch/title + labels | sonnet | **yes** | edits the shared if/else — risk of regressing non-wave paths |
| 10 Label plumbing + provisioning | **opus** | **yes** | GitHub label API + threading through 3 layers |
| 11 Idempotency guard | **opus** | **yes** | safety guard; subtle `get_issues(labels=…)` + `pull_request` filter |
| 12 `deploy-strategy` action input | **haiku** | no | trivial YAML wiring |
| 13 Full suite green + supersede #19 | sonnet | **yes (final full-diff)** | whole-PR Codex review (`base: main`) before handoff |

---

## Task 0: Branch

- [ ] **Step 1: Create the working branch off main**

```bash
cd ~/keboola/devel/helm-image-updater
git checkout main && git pull
git checkout -b ST-4034-deploy-strategy-wave
```

---

## Task 1: `DeployStrategy` enum + `DEPLOY_STRATEGY` parsing

**Files:**
- Modify: `helm_image_updater/models.py` (after the `UpdateStrategy` enum, ~line 14)
- Modify: `helm_image_updater/environment.py` (`EnvironmentConfig` + `from_env`)
- Test: `tests/test_deploy_strategy.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_deploy_strategy.py
"""Tests for DEPLOY_STRATEGY parsing, validation, and wave helpers (PR-A)."""

from helm_image_updater.environment import EnvironmentConfig
from helm_image_updater.models import DeployStrategy


def _base_env(**overrides):
    env = {
        "HELM_CHART": "dummy-service",
        "IMAGE_TAG": "production-abc123",
        "GH_TOKEN": "t",
        "GH_APPROVE_TOKEN": "a",
    }
    env.update(overrides)
    return env


def test_deploy_strategy_defaults_to_standard():
    cfg = EnvironmentConfig.from_env(_base_env())
    assert cfg.deploy_strategy == DeployStrategy.STANDARD


def test_deploy_strategy_parses_known_values():
    for raw, expected in [
        ("standard", DeployStrategy.STANDARD),
        ("cloud_multi_stage", DeployStrategy.CLOUD_MULTI_STAGE),
        ("gradual", DeployStrategy.GRADUAL),
        ("critical", DeployStrategy.CRITICAL),
        ("critical-manual-gate", DeployStrategy.CRITICAL_MANUAL_GATE),
    ]:
        cfg = EnvironmentConfig.from_env(_base_env(DEPLOY_STRATEGY=raw))
        assert cfg.deploy_strategy == expected


def test_multi_stage_true_aliases_to_cloud_multi_stage_when_unset():
    cfg = EnvironmentConfig.from_env(_base_env(MULTI_STAGE="true"))
    assert cfg.deploy_strategy == DeployStrategy.CLOUD_MULTI_STAGE


def test_unknown_deploy_strategy_does_not_silently_become_standard():
    cfg = EnvironmentConfig.from_env(_base_env(DEPLOY_STRATEGY="gradul"))
    # Parsed value stays unset/standard, but an error is recorded for validate() (Task 2).
    assert cfg._deploy_strategy_error is not None


def test_cloud_multi_stage_sets_multi_stage_flag():
    # DEPLOY_STRATEGY=cloud_multi_stage must drive the legacy multi_stage grouping branch.
    cfg = EnvironmentConfig.from_env(_base_env(DEPLOY_STRATEGY="cloud_multi_stage"))
    assert cfg.deploy_strategy == DeployStrategy.CLOUD_MULTI_STAGE
    assert cfg.multi_stage is True


def test_explicit_standard_overrides_multi_stage_flag():
    # DEPLOY_STRATEGY=standard wins over MULTI_STAGE=true: multi_stage must be False
    # (the warning says MULTI_STAGE is ignored — the flag must actually reflect that).
    cfg = EnvironmentConfig.from_env(_base_env(DEPLOY_STRATEGY="standard", MULTI_STAGE="true"))
    assert cfg.deploy_strategy == DeployStrategy.STANDARD
    assert cfg.multi_stage is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_deploy_strategy.py -v`
Expected: FAIL — `ImportError: cannot import name 'DeployStrategy'`.

- [ ] **Step 3: Add the enum to `models.py`**

Insert after the `UpdateStrategy` enum (after line 14):

```python
class DeployStrategy(Enum):
    """Deploy strategy (the DEPLOY_STRATEGY knob). Values double as the `deploy:*`
    label value for promoter-managed (wave) strategies."""
    STANDARD = "standard"
    CLOUD_MULTI_STAGE = "cloud_multi_stage"
    GRADUAL = "gradual"
    CRITICAL = "critical"
    CRITICAL_MANUAL_GATE = "critical-manual-gate"

    @property
    def is_wave(self) -> bool:
        return self in (
            DeployStrategy.GRADUAL,
            DeployStrategy.CRITICAL,
            DeployStrategy.CRITICAL_MANUAL_GATE,
        )
```

- [ ] **Step 4: Parse it in `environment.py`**

Add the import at the top of `environment.py`:

```python
from .models import DeployStrategy
```

Add the field to `EnvironmentConfig` (next to `multi_stage`, ~line 21):

```python
    deploy_strategy: DeployStrategy = DeployStrategy.STANDARD
    _deploy_strategy_error: Optional[str] = field(default=None, init=False, repr=False)
```

In `from_env`, before the `config = cls(...)` call, parse the raw value:

```python
        # Parse DEPLOY_STRATEGY (default standard). MULTI_STAGE=true is a deprecated
        # alias for cloud_multi_stage when DEPLOY_STRATEGY is not explicitly set.
        raw_strategy = env.get("DEPLOY_STRATEGY", "").strip().lower()
        multi_stage_raw = env.get("MULTI_STAGE", "false").lower() == "true"
        deploy_strategy = DeployStrategy.STANDARD
        deploy_strategy_error = None
        if raw_strategy:
            try:
                deploy_strategy = DeployStrategy(raw_strategy)
            except ValueError:
                deploy_strategy_error = (
                    f"Invalid DEPLOY_STRATEGY: '{raw_strategy}'. "
                    "Must be one of: standard, cloud_multi_stage, gradual, critical, critical-manual-gate"
                )
            if multi_stage_raw and deploy_strategy != DeployStrategy.CLOUD_MULTI_STAGE:
                print("WARNING: MULTI_STAGE=true is ignored because DEPLOY_STRATEGY is set explicitly")
        elif multi_stage_raw:
            deploy_strategy = DeployStrategy.CLOUD_MULTI_STAGE

        # The resolved strategy is the SINGLE source of truth for multi_stage: an explicit
        # DEPLOY_STRATEGY wins over MULTI_STAGE, and an unset DEPLOY_STRATEGY with
        # MULTI_STAGE=true already resolved to CLOUD_MULTI_STAGE above. (Do NOT OR in
        # multi_stage_raw — that would keep multi_stage=True for DEPLOY_STRATEGY=standard
        # MULTI_STAGE=true, contradicting the "ignored" warning.)
        multi_stage = deploy_strategy == DeployStrategy.CLOUD_MULTI_STAGE
```

In the `cls(...)` constructor call, **replace** the existing
`multi_stage=env.get("MULTI_STAGE", "false").lower() == "true",` line with
`multi_stage=multi_stage,` and add `deploy_strategy=deploy_strategy,`. After the call set:

```python
        config._deploy_strategy_error = deploy_strategy_error
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_deploy_strategy.py -v`
Expected: PASS (4 tests).

- [ ] **Step 6: Commit**

```bash
git add helm_image_updater/models.py helm_image_updater/environment.py tests/test_deploy_strategy.py
git commit -m "feat: add DeployStrategy enum + DEPLOY_STRATEGY parsing (MULTI_STAGE alias)"
```

---

## Task 2: Validation — unknown errors, wave requires production tag

**Files:**
- Modify: `helm_image_updater/environment.py` (`validate`)
- Test: `tests/test_deploy_strategy.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_deploy_strategy.py  (append)
def test_unknown_deploy_strategy_is_a_validation_error():
    cfg = EnvironmentConfig.from_env(_base_env(DEPLOY_STRATEGY="gradul"))
    errors = cfg.validate()
    assert any("Invalid DEPLOY_STRATEGY" in e for e in errors)


def test_wave_strategy_requires_production_tag():
    cfg = EnvironmentConfig.from_env(_base_env(IMAGE_TAG="dev-abc", DEPLOY_STRATEGY="gradual"))
    errors = cfg.validate()
    assert any("requires a production" in e for e in errors)


def test_wave_strategy_ok_with_production_tag():
    cfg = EnvironmentConfig.from_env(_base_env(IMAGE_TAG="production-abc", DEPLOY_STRATEGY="gradual"))
    assert cfg.validate() == []


def test_wave_strategy_rejected_with_override_stack():
    cfg = EnvironmentConfig.from_env(
        _base_env(DEPLOY_STRATEGY="critical", OVERRIDE_STACK="kbc-us-east-1")
    )
    errors = cfg.validate()
    assert any("OVERRIDE_STACK" in e for e in errors)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_deploy_strategy.py -v`
Expected: FAIL — the new assertions fail (no such validation yet).

- [ ] **Step 3: Add validation rules in `environment.py` `validate()`**

At the end of `validate()`, before `return errors`, add:

```python
        # DEPLOY_STRATEGY validation
        if self._deploy_strategy_error:
            errors.append(self._deploy_strategy_error)

        if self.deploy_strategy.is_wave:
            if self.override_stack:
                errors.append("DEPLOY_STRATEGY wave modes are incompatible with OVERRIDE_STACK")
            elif not self.image_tag:
                errors.append(
                    f"DEPLOY_STRATEGY '{self.deploy_strategy.value}' requires a production/semver IMAGE_TAG"
                )
            else:
                tag_type = detect_tag_type(self.image_tag)
                if tag_type not in (TagType.PRODUCTION, TagType.SEMVER):
                    errors.append(
                        f"DEPLOY_STRATEGY '{self.deploy_strategy.value}' requires a production/semver "
                        f"IMAGE_TAG, got '{self.image_tag}'"
                    )
```

Add the matching test:

```python
# tests/test_deploy_strategy.py  (append)
def test_wave_strategy_requires_image_tag_not_just_extra_tag():
    cfg = EnvironmentConfig.from_env({
        "HELM_CHART": "dummy-service", "GH_TOKEN": "t", "GH_APPROVE_TOKEN": "a",
        "EXTRA_TAG1": "image.tag:production-abc", "DEPLOY_STRATEGY": "gradual",
    })
    errors = cfg.validate()
    assert any("IMAGE_TAG" in e for e in errors)
```

(`detect_tag_type` and `TagType` are already imported at the top of `validate()`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_deploy_strategy.py -v`
Expected: PASS (all tests).

- [ ] **Step 5: Commit**

```bash
git add helm_image_updater/environment.py tests/test_deploy_strategy.py
git commit -m "feat: validate DEPLOY_STRATEGY (unknown=error, wave requires prod tag)"
```

---

## Task 3: `PRPlan.labels` field

**Files:**
- Modify: `helm_image_updater/models.py` (`PRPlan`, ~line 36)
- Test: `tests/test_deploy_strategy.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_deploy_strategy.py  (append)
from helm_image_updater.models import PRPlan


def test_prplan_labels_defaults_empty():
    p = PRPlan(
        branch_name="b", pr_title="t", pr_body="body", base_branch="main",
        auto_merge=False, files_to_commit=[], commit_message="c",
    )
    assert p.labels == []


def test_prplan_labels_can_be_set():
    p = PRPlan(
        branch_name="b", pr_title="t", pr_body="body", base_branch="main",
        auto_merge=False, files_to_commit=[], commit_message="c",
        labels=["release:wave:0"],
    )
    assert p.labels == ["release:wave:0"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_deploy_strategy.py -k prplan_labels -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'labels'`.

- [ ] **Step 3: Add the field to `PRPlan` in `models.py`**

Add as the last field of `PRPlan` (after `commit_message`):

```python
    labels: List[str] = field(default_factory=list)
```

(`field` and `List` are already imported in `models.py`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_deploy_strategy.py -k prplan_labels -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add helm_image_updater/models.py tests/test_deploy_strategy.py
git commit -m "feat: add PRPlan.labels field"
```

---

## Task 4: `compute_release_id` (length-bounded, deterministic slug)

**Files:**
- Create: `helm_image_updater/wave_planning.py`
- Test: `tests/test_deploy_strategy.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_deploy_strategy.py  (append)
from helm_image_updater.wave_planning import compute_release_id, release_id_label

GH_LABEL_MAX = 50


def test_release_id_label_fits_github_limit_even_for_long_inputs():
    rid = compute_release_id("infrastructure-plugin-update-components",
                             "production-633743c4fc2431d3a9727987a3152a8ea5ec38c2")
    assert len(release_id_label(rid)) <= GH_LABEL_MAX


def test_release_id_is_deterministic():
    a = compute_release_id("connection", "production-abc123")
    b = compute_release_id("connection", "production-abc123")
    assert a == b


def test_release_id_distinct_for_distinct_tags():
    a = compute_release_id("connection", "production-aaaa")
    b = compute_release_id("connection", "production-bbbb")
    assert a != b
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_deploy_strategy.py -k release_id -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'helm_image_updater.wave_planning'`.

- [ ] **Step 3: Create `wave_planning.py` with the helpers**

```python
# helm_image_updater/wave_planning.py
"""Pure helpers for promoter-managed (wave) deploys.

No I/O — only data transformation. Consumed by plan_builder.
"""

import hashlib
from typing import Dict, List, Optional

from .models import DeployStrategy
from .stack_classification import classify_stack

# GitHub caps label names at 50 chars; "release:id:" eats 11, leaving 39.
_RELEASE_ID_PREFIX = "release:id:"
_RELEASE_ID_MAX = 50 - len(_RELEASE_ID_PREFIX)  # 39
_HASH_LEN = 12


def compute_release_id(helm_chart: str, image_tag: str) -> str:
    """A stable, collision-resistant, length-bounded grouping key (<chart>-<hash>).

    Promoter treats this as opaque (it gets the app from the PR diff), so the only
    invariants are: fits the label limit, deterministic, unique per (chart, tag).
    """
    digest = hashlib.sha256(f"{helm_chart}\0{image_tag}".encode()).hexdigest()[:_HASH_LEN]
    chart_room = _RELEASE_ID_MAX - 1 - _HASH_LEN  # room for "<chart>-"
    chart = helm_chart[:chart_room]
    return f"{chart}-{digest}"


def release_id_label(release_id: str) -> str:
    return f"{_RELEASE_ID_PREFIX}{release_id}"


def wave_label(wave: int) -> str:
    return f"release:wave:{wave}"


def deploy_label(strategy: DeployStrategy) -> str:
    return f"deploy:{strategy.value}"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_deploy_strategy.py -k release_id -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add helm_image_updater/wave_planning.py tests/test_deploy_strategy.py
git commit -m "feat: add compute_release_id + label builders (wave_planning)"
```

---

## Task 5: `resolve_wave` (per-stack wave assignment)

**Files:**
- Modify: `helm_image_updater/wave_planning.py`
- Test: `tests/test_deploy_strategy.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_deploy_strategy.py  (append)
from helm_image_updater.wave_planning import resolve_wave


def test_resolve_wave_uses_explicit_value():
    assert resolve_wave("kbc-us-east-1", {"rollout_wave": 2}) == 2


def test_resolve_wave_dev_defaults_to_0_when_missing():
    # dev-keboola-gcp-us-central1 is a dev stack (DEV_STACK_MAPPING)
    assert resolve_wave("dev-keboola-gcp-us-central1", None) == 0
    assert resolve_wave("dev-keboola-gcp-us-central1", {}) == 0


def test_resolve_wave_non_dev_defaults_to_3_when_missing():
    assert resolve_wave("kbc-us-east-1", None) == 3


def test_resolve_wave_explicit_overrides_dev_default():
    assert resolve_wave("dev-keboola-gcp-us-central1", {"rollout_wave": 1}) == 1


def test_resolve_wave_rejects_out_of_range():
    import pytest
    with pytest.raises(ValueError):
        resolve_wave("kbc-us-east-1", {"rollout_wave": 5})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_deploy_strategy.py -k resolve_wave -v`
Expected: FAIL — `ImportError: cannot import name 'resolve_wave'`.

- [ ] **Step 3: Add `resolve_wave` to `wave_planning.py`**

```python
def resolve_wave(stack: str, metadata: Optional[Dict]) -> int:
    """wave(stack) = explicit `rollout_wave` if present (integer 0..3), else dev->0 / other->3."""
    if metadata and "rollout_wave" in metadata:
        raw = metadata["rollout_wave"]
        # Reject non-integers (and bool, which is an int subclass) — silent coercion of a
        # float/str/bool rollout_wave would mask a prod misconfiguration.
        if isinstance(raw, bool) or not isinstance(raw, int):
            raise ValueError(f"rollout_wave for {stack} must be an integer 0..3, got {raw!r}")
        if raw < 0 or raw > 3:
            raise ValueError(f"rollout_wave for {stack} must be 0..3, got {raw}")
        return raw
    return 0 if classify_stack(stack).is_dev else 3
```

(Codex hardening — also add a test asserting `resolve_wave("kbc-us-east-1", {"rollout_wave": 1.9})` and
`{"rollout_wave": True}` both raise `ValueError`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_deploy_strategy.py -k resolve_wave -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add helm_image_updater/wave_planning.py tests/test_deploy_strategy.py
git commit -m "feat: add resolve_wave (explicit rollout_wave, else dev=0/other=3)"
```

---

## Task 6: Wave grouping dispatch in `plan_builder`

**Files:**
- Modify: `helm_image_updater/plan_builder.py` (`_group_changes_for_prs`, ~line 401; imports ~line 13-25)
- Test: `tests/test_wave_grouping.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_wave_grouping.py
"""Wave-mode grouping, auto-merge, labels, idempotency (PR-A)."""

from unittest.mock import Mock
from helm_image_updater.models import UpdateStrategy, DeployStrategy
from helm_image_updater.plan_builder import _group_changes_for_prs


def _stack_change(stack):
    return {"stack": stack, "file_change": Mock(), "changes": []}


def _wave_metadata(by_stack):
    """Return a read_yaml side_effect mapping <stack>/stack-metadata.yaml -> dict."""
    def _read(path):
        for stack, wave in by_stack.items():
            if path == f"{stack}/stack-metadata.yaml":
                return {"rollout_wave": wave}
        return None
    return _read


def test_wave_grouping_one_pr_per_wave_with_labels():
    waves = {
        "dev-keboola-gcp-us-central1": 0,
        "com-keboola-azure-north-europe": 1,
        "kbc-us-east-1": 2,
        "cloud-keboola-cs": 3,
    }
    io = Mock()
    io.read_yaml.side_effect = _wave_metadata(waves)

    config = Mock()
    config.deploy_strategy = DeployStrategy.GRADUAL

    plan = Mock()
    plan.strategy = UpdateStrategy.PRODUCTION
    plan.multi_stage = False
    plan.helm_chart = "dummy-service"
    plan.image_tag = "production-abc123"

    groups = _group_changes_for_prs(
        [_stack_change(s) for s in waves], plan, config, io
    )

    assert len(groups) == 4
    by_wave = {g["wave_number"]: g for g in groups}
    assert set(by_wave) == {0, 1, 2, 3}
    g1 = by_wave[1]
    assert g1["pr_type"] == "wave"
    assert g1["stacks"] == ["com-keboola-azure-north-europe"]
    assert any(l.startswith("release:id:") for l in g1["labels"])
    assert "release:wave:1" in g1["labels"]
    assert "deploy:gradual" in g1["labels"]


def test_wave_grouping_requires_all_waves_0_to_3():
    import pytest
    waves = {"dev-keboola-gcp-us-central1": 0, "kbc-us-east-1": 1}  # missing 2 and 3
    io = Mock()
    io.read_yaml.side_effect = _wave_metadata(waves)
    config = Mock(); config.deploy_strategy = DeployStrategy.GRADUAL
    plan = Mock(); plan.strategy = UpdateStrategy.PRODUCTION; plan.multi_stage = False
    plan.helm_chart = "dummy-service"; plan.image_tag = "production-abc"

    with pytest.raises(RuntimeError, match="wave"):
        _group_changes_for_prs([_stack_change(s) for s in waves], plan, config, io)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_wave_grouping.py -v`
Expected: FAIL — wave grouping not implemented (`KeyError: 'wave_number'` or all stacks in one group).

- [ ] **Step 3: Add the wave branch to `_group_changes_for_prs`**

Add imports at the top of `plan_builder.py` (extend the existing `.models` import and add wave_planning):

```python
from .models import UpdatePlan, FileChange, PRPlan, UpdateStrategy, TagChange, DeployStrategy
from .wave_planning import compute_release_id, release_id_label, wave_label, deploy_label, resolve_wave
```

At the **start** of `_group_changes_for_prs` (before the canary check at line 409), insert the wave dispatch:

```python
    # Promoter-managed wave strategies: one PR per wave (0..3), unmerged, labeled.
    if config.deploy_strategy.is_wave:
        return _group_changes_by_wave(stack_changes, plan, config, io_layer)
```

Then add the new function (next to `_group_changes_for_prs`):

```python
def _group_changes_by_wave(stack_changes, plan, config, io_layer):
    """Group changes into one PR per rollout wave (0..3) for promoter consumption."""
    # Never roll an e2e stack into a production wave (defensive — known e2e are also in EXCLUDED_STACKS).
    stack_changes = [sc for sc in stack_changes if not sc['stack'].endswith('-e2e')]
    release_id = compute_release_id(plan.helm_chart, plan.image_tag)
    deploy_lbl = deploy_label(config.deploy_strategy)

    by_wave = {}
    for sc in stack_changes:
        metadata = io_layer.read_yaml(f"{sc['stack']}/stack-metadata.yaml")
        wave = resolve_wave(sc['stack'], metadata)
        by_wave.setdefault(wave, []).append(sc)

    present = set(by_wave)
    required = {0, 1, 2, 3}
    if present != required:
        missing = sorted(required - present)
        raise RuntimeError(
            f"Wave deploy requires non-empty waves 0..3 (promoter needs a contiguous "
            f"release:wave:0..3); missing/empty waves: {missing}. "
            f"Check rollout_wave in stack-metadata.yaml across target stacks."
        )

    groups = []
    for wave in sorted(by_wave):
        changes = by_wave[wave]
        groups.append({
            'stacks': [sc['stack'] for sc in changes],
            'changes': changes,
            'base_branch': 'main',
            'pr_type': 'wave',
            'wave_number': wave,
            'release_id': release_id,
            'labels': [release_id_label(release_id), wave_label(wave), deploy_lbl],
        })
    return groups
```

- [ ] **Step 3b: Fix existing grouping tests that build a `Mock()` config**

Introducing `config.deploy_strategy.is_wave` means any test that passes a bare `Mock()` as
`config` to `_group_changes_for_prs` now hits the wave branch (a Mock attribute is truthy). In
`tests/test_plan_builder.py`, every such test (e.g. `test_multi_cloud_grouping_non_multi_stage`,
`test_multi_cloud_grouping_dev_strategy`, and any multi-stage grouping test) constructs
`mock_config = Mock()` — add this line to each, right after that construction:

```python
    from helm_image_updater.models import DeployStrategy
    mock_config.deploy_strategy = DeployStrategy.STANDARD
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_wave_grouping.py tests/test_plan_builder.py -v`
Expected: PASS — the 2 new wave tests AND all existing grouping tests (after the Step 3b additions).

- [ ] **Step 5: Commit**

```bash
git add helm_image_updater/plan_builder.py tests/test_wave_grouping.py tests/test_plan_builder.py
git commit -m "feat: wave grouping (one PR per wave 0..3 with labels) + contiguity guard"
```

---

## Task 7: `standard` + `automerge=false` → single PR (behavior change)

**Files:**
- Modify: `helm_image_updater/plan_builder.py` (`_group_changes_for_prs` default tail, ~line 471-490)
- Test: `tests/test_plan_builder.py` (append)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_plan_builder.py  (append, near the other grouping tests)
def test_standard_production_automerge_false_is_single_pr():
    """standard + automerge=false now produces ONE unmerged PR (was per-stack)."""
    from helm_image_updater.plan_builder import _group_changes_for_prs
    from helm_image_updater.models import DeployStrategy

    mock_io_layer = Mock()
    mock_config = Mock()
    mock_config.automerge = False
    mock_config.deploy_strategy = DeployStrategy.STANDARD

    mock_plan = Mock()
    mock_plan.multi_stage = False
    mock_plan.strategy = UpdateStrategy.PRODUCTION

    stacks = ["com-keboola-gcp-prod", "com-keboola-azure-prod", "com-keboola-aws-prod"]
    stack_changes = [{"stack": s, "file_change": Mock(), "changes": []} for s in stacks]

    groups = _group_changes_for_prs(stack_changes, mock_plan, mock_config, mock_io_layer)

    assert len(groups) == 1
    assert len(groups[0]["stacks"]) == 3
    assert groups[0]["pr_type"] == "standard"
```

Note: the existing `Mock()`-config grouping tests were already fixed in Task 6 Step 3b (set
`deploy_strategy = STANDARD`); this task only *adds* the new automerge=false test.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_plan_builder.py -k standard_production_automerge_false -v`
Expected: FAIL — current code returns 3 per-stack groups, not 1.

- [ ] **Step 3: Change the default tail of `_group_changes_for_prs`**

Replace the production `else` (per-stack) branch (lines ~480-490) so production always returns a single group:

```python
    # Production without multi-stage: a single PR regardless of automerge.
    # (automerge=false now yields ONE unmerged PR, not one-per-stack.)
    return [{
        'stacks': [sc['stack'] for sc in stack_changes],
        'changes': stack_changes,
        'base_branch': 'main',
        'pr_type': 'standard'
    }]
```

(The `if config.automerge:` single-PR branch immediately above becomes redundant — collapse both into this single return.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_plan_builder.py tests/test_cli_functional.py -v`
Expected: PASS. If any functional test asserted the *old* per-stack prod `automerge=false` behavior
(N PRs), update it to expect **1 PR** — that is the intended behavior change for this task.

- [ ] **Step 5: Commit**

```bash
git add helm_image_updater/plan_builder.py tests/
git commit -m "feat: standard production automerge=false now creates one unmerged PR"
```

---

## Task 8: `_should_auto_merge` — wave PRs never auto-merge

**Files:**
- Modify: `helm_image_updater/plan_builder.py` (`_should_auto_merge`, ~line 600)
- Test: `tests/test_wave_grouping.py` (append)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_wave_grouping.py  (append)
from helm_image_updater.plan_builder import _should_auto_merge


def test_wave_pr_type_never_auto_merges():
    plan = Mock(); plan.strategy = UpdateStrategy.PRODUCTION
    assert _should_auto_merge(plan, "wave", user_requested=True) is False
    assert _should_auto_merge(plan, "wave", user_requested=False) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_wave_grouping.py -k auto_merge -v`
Expected: FAIL — returns `True` (user_requested) for `pr_type='wave'`.

- [ ] **Step 3: Add the wave rule in `_should_auto_merge`**

**Before** the canary check (so `pr_type=='wave'` is literal regardless of strategy — a wave PR is
never auto-merged even if some future caller passes a non-production strategy):

```python
    if pr_type == 'wave':
        print(f"       - result: FALSE (wave PRs are merged by release-promoter)")
        return False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_wave_grouping.py -k auto_merge -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add helm_image_updater/plan_builder.py tests/test_wave_grouping.py
git commit -m "feat: wave PRs are never auto-merged by HIU (promoter owns merges)"
```

---

## Task 9: `_create_pr_plan` — wave branch/title + labels

**Files:**
- Modify: `helm_image_updater/plan_builder.py` (`_create_pr_plan`, ~line 493)
- Test: `tests/test_wave_grouping.py` (append)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_wave_grouping.py  (append)
from helm_image_updater.plan_builder import _create_pr_plan


def test_create_pr_plan_wave_sets_labels_and_branch_title():
    config = Mock(); config.automerge = False
    config.deploy_strategy = DeployStrategy.GRADUAL
    plan = Mock()
    plan.strategy = UpdateStrategy.PRODUCTION
    plan.multi_stage = False
    plan.helm_chart = "dummy-service"
    plan.image_tag = "production-abc123"
    plan.extra_tags = []
    plan.metadata = {}

    fc = Mock(); fc.file_path = "kbc-us-east-1/dummy-service/tag.yaml"
    group = {
        'stacks': ["kbc-us-east-1"],
        'changes': [{"stack": "kbc-us-east-1", "file_change": fc, "changes": []}],
        'base_branch': 'main',
        'pr_type': 'wave',
        'wave_number': 2,
        'release_id': 'dummy-service-deadbeef0123',
        'labels': ["release:id:dummy-service-deadbeef0123", "release:wave:2", "deploy:gradual"],
    }

    pr_plan = _create_pr_plan(group, plan, config)

    assert pr_plan.labels == group['labels']
    assert pr_plan.auto_merge is False
    assert "wave2" in pr_plan.branch_name
    assert "wave 2" in pr_plan.pr_title
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_wave_grouping.py -k create_pr_plan_wave -v`
Expected: FAIL — `pr_plan.labels == []` and branch/title lack wave markers.

- [ ] **Step 3: Handle `pr_type='wave'` in `_create_pr_plan`**

In the branch-name `if/elif/else` (lines ~505-515), add a `wave` branch before the `else`:

```python
    elif pr_type == 'wave':
        wave = pr_group['wave_number']
        branch_name = f"{plan.helm_chart}-wave{wave}-{plan.image_tag}-{suffix}"
```

Replace the PR-title block (lines ~528-544) so wave PRs get a dedicated title (skip `generate_pr_title_prefix` for waves):

```python
    if pr_type == 'wave':
        wave = pr_group['wave_number']
        pr_title = f"[{plan.helm_chart} {config.deploy_strategy.value} wave {wave}] " \
                   f"{plan.helm_chart}@{plan.image_tag}"
    else:
        pr_title_prefix = generate_pr_title_prefix(
            strategy=plan.strategy,
            is_multi_stage=plan.multi_stage,
            user_requested_automerge=config.automerge,
            target_stacks=pr_group['stacks'],
            cloud_provider=pr_group.get('cloud_provider'),
        )
        pr_title = generate_pr_title(
            pr_title_prefix=pr_title_prefix,
            helm_chart=plan.helm_chart,
            image_tag=plan.image_tag,
            extra_tags=plan.extra_tags,
            target_stacks=pr_group['stacks'],
        )
```

Finally, pass labels into the returned `PRPlan(...)` (add as a kwarg):

```python
        labels=pr_group.get('labels', []),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_wave_grouping.py -k create_pr_plan_wave -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add helm_image_updater/plan_builder.py tests/test_wave_grouping.py
git commit -m "feat: wave PR branch/title + carry labels into PRPlan"
```

---

## Task 10: Thread labels through executor → io_layer (provision + apply)

**Files:**
- Modify: `helm_image_updater/io_layer.py` (`create_pull_request` ~240, `create_branch_commit_and_pr` ~378; add `_ensure_labels_exist`)
- Modify: `helm_image_updater/plan_executor.py` (`_execute_pr_plans` ~69, dry-run ~72)
- Test: `tests/test_wave_grouping.py` (append)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_wave_grouping.py  (append)
from helm_image_updater.io_layer import IOLayer
from github.GithubException import GithubException


def test_create_pull_request_provisions_and_applies_labels():
    repo = Mock()
    # get_label raises 404 for the dynamic release:id label, succeeds otherwise
    def _get_label(name):
        if name.startswith("release:id:"):
            raise GithubException(404, {"message": "Not Found"}, None)
        return Mock()
    repo.get_label.side_effect = _get_label
    pr = Mock(); pr.html_url = "http://x/1"; pr.number = 1
    repo.create_pull.return_value = pr

    io = IOLayer(Mock(), repo, dry_run=False, approve_github_repo=Mock())
    io.push_branch = Mock()  # avoid real git push

    io.create_pull_request(
        title="t", body="b", branch_name="br", base_branch="main",
        auto_merge=False,
        labels=["release:id:dummy-service-deadbeef0123", "release:wave:2", "deploy:gradual"],
    )

    repo.create_label.assert_called()  # created the missing release:id label
    pr.add_to_labels.assert_called_once_with(
        "release:id:dummy-service-deadbeef0123", "release:wave:2", "deploy:gradual"
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_wave_grouping.py -k provisions_and_applies_labels -v`
Expected: FAIL — `create_pull_request() got an unexpected keyword argument 'labels'`.

- [ ] **Step 3: Add label support to `io_layer.py`**

Add a helper method on `IOLayer`:

```python
    def _ensure_labels_exist(self, names: List[str]) -> None:
        """Create-if-missing each label (tolerate already-exists)."""
        for name in names:
            try:
                self.github_repo.get_label(name)
            except GithubException as e:
                if e.status == 404:
                    try:
                        self.github_repo.create_label(name=name, color="ededed")
                    except GithubException as ce:
                        if ce.status != 422:  # 422 = already exists (race) → fine
                            raise
                else:
                    raise
```

Change `create_pull_request` signature to accept `labels`:

```python
    def create_pull_request(
        self,
        title: str,
        body: str,
        branch_name: str,
        base_branch: str = "main",
        auto_merge: bool = False,
        labels: Optional[List[str]] = None,
    ) -> Optional[str]:
```

In the dry-run block, print labels:

```python
            if labels:
                print(f"[DRY RUN] Labels: {', '.join(labels)}")
```

After `pr = self.github_repo.create_pull(...)` and the `print(f"PR created: ...")`, before the auto-merge block, apply labels:

```python
        if labels:
            self._ensure_labels_exist(labels)
            pr.add_to_labels(*labels)
            print(f"🏷️  Applied labels: {', '.join(labels)}")
```

Change `create_branch_commit_and_pr` signature to accept and forward `labels`:

```python
    def create_branch_commit_and_pr(
        self,
        branch_name: str,
        files_to_commit: List[str],
        commit_message: str,
        pr_title: str,
        pr_body: str,
        base_branch: str = "main",
        auto_merge: bool = False,
        labels: Optional[List[str]] = None,
    ) -> Optional[str]:
```

Find the `self.create_pull_request(...)` call inside `create_branch_commit_and_pr` (in the lines after 399) and add `labels=labels` to it.

- [ ] **Step 4: Forward labels from the executor**

In `plan_executor.py` `_execute_pr_plans`, dry-run block (after line 77) add:

```python
            if pr_plan.labels:
                print(f"  Labels: {', '.join(pr_plan.labels)}")
```

And in the real call (line 85), add `labels=pr_plan.labels`:

```python
            pr_url = io_layer.create_branch_commit_and_pr(
                branch_name=pr_plan.branch_name,
                files_to_commit=pr_plan.files_to_commit,
                commit_message=pr_plan.commit_message,
                pr_title=pr_plan.pr_title,
                pr_body=pr_plan.pr_body,
                base_branch=pr_plan.base_branch,
                auto_merge=pr_plan.auto_merge,
                labels=pr_plan.labels,
            )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_wave_grouping.py -k provisions_and_applies_labels -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add helm_image_updater/io_layer.py helm_image_updater/plan_executor.py tests/test_wave_grouping.py
git commit -m "feat: provision + apply PR labels through executor and io_layer"
```

---

## Task 11: Idempotency — skip when `release:id` already exists

**Files:**
- Modify: `helm_image_updater/io_layer.py` (add `find_prs_by_label`)
- Modify: `helm_image_updater/plan_builder.py` (`prepare_plan`, after grouping ~line 88)
- Test: `tests/test_wave_grouping.py` (append)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_wave_grouping.py  (append)
import pytest
from helm_image_updater.plan_builder import _guard_release_not_already_open


def test_guard_raises_when_release_id_already_has_open_prs():
    io = Mock()
    io.find_prs_by_label.return_value = [101, 102]  # existing open PRs
    with pytest.raises(RuntimeError, match="already"):
        _guard_release_not_already_open("dummy-service-deadbeef0123", io)


def test_guard_passes_when_no_existing_prs():
    io = Mock()
    io.find_prs_by_label.return_value = []
    # Should not raise.
    _guard_release_not_already_open("dummy-service-deadbeef0123", io)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_wave_grouping.py -k guard -v`
Expected: FAIL — `ImportError: cannot import name '_guard_release_not_already_open'`.

- [ ] **Step 3: Add `find_prs_by_label` to io_layer and the guard to plan_builder**

In `io_layer.py` (GitHub Operations section):

```python
    def find_prs_by_label(self, label: str) -> List[int]:
        """Return open PR numbers carrying the given label (idempotency check)."""
        numbers = []
        for issue in self.github_repo.get_issues(state="open", labels=[label]):
            if issue.pull_request is not None:
                numbers.append(issue.number)
        return numbers
```

In `plan_builder.py`, add the guard function and import `release_id_label` (already imported in Task 6):

```python
def _guard_release_not_already_open(release_id: str, io_layer: IOLayer) -> None:
    """Fail loudly if a release with this id already has open PRs (re-run safety).

    Creating a second set of wave PRs with the same release:id would give promoter
    duplicate release:wave:N labels -> ¬wellFormed -> Conflicted.
    """
    existing = io_layer.find_prs_by_label(release_id_label(release_id))
    if existing:
        raise RuntimeError(
            f"Release '{release_id}' already has open PRs {existing}. "
            f"Refusing to create duplicate wave PRs (would make the release Conflicted). "
            f"Close/finish the existing release first."
        )
```

Wire it into `prepare_plan` — in wave mode, before creating PR plans (after `pr_groups = _group_changes_for_prs(...)`, ~line 88), and skip in dry-run:

```python
    # Idempotency: in wave mode, never fan out a duplicate release.
    if config.deploy_strategy.is_wave and not config.dry_run and pr_groups:
        _guard_release_not_already_open(pr_groups[0]['release_id'], io_layer)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_wave_grouping.py -k guard -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add helm_image_updater/io_layer.py helm_image_updater/plan_builder.py tests/test_wave_grouping.py
git commit -m "feat: idempotency guard — refuse duplicate wave PRs for an existing release:id"
```

---

## Task 12: Action input `deploy-strategy`

**Files:**
- Modify: `action.yaml` (inputs ~line 18-21; env mapping in the run step)

- [ ] **Step 1: Add the input**

After the `multi-stage` input (line ~18-20), add:

```yaml
  deploy-strategy:
    description: 'Promoter deploy strategy: standard | cloud_multi_stage | gradual | critical | critical-manual-gate'
    required: false
    default: ''
```
> **Default is empty, NOT `standard`.** The action always forwards the input as `DEPLOY_STRATEGY`, and
> `from_env` treats any non-empty value as *explicit* (overriding `MULTI_STAGE`). An empty default lets
> legacy `multi-stage: true` callers keep aliasing to `cloud_multi_stage`; empty → unset → `standard`.

- [ ] **Step 2: Map it to the `DEPLOY_STRATEGY` env var**

Find the step that maps inputs → env (where `MULTI_STAGE`, `IMAGE_TAG`, etc. are set) and add:

```yaml
        DEPLOY_STRATEGY: ${{ inputs.deploy-strategy }}
```

- [ ] **Step 3: Verify the mapping is present**

Run: `grep -n 'deploy-strategy\|DEPLOY_STRATEGY' action.yaml`
Expected: the input definition and the env mapping both appear.

- [ ] **Step 4: Commit**

```bash
git add action.yaml
git commit -m "feat: add deploy-strategy action input -> DEPLOY_STRATEGY"
```

---

## Task 13: Full suite green + docs

**Files:**
- Run: full test suite
- Modify: `deploy-strategy-promoter-integration.md` (mark spec implemented — optional)

- [ ] **Step 1: Run the entire test suite**

Run: `python -m pytest -v`
Expected: PASS (all existing + new tests).

- [ ] **Step 2: Manual dry-run sanity check (optional, local)**

```bash
# From a kbc-stacks-like checkout (or the testing repo with rollout_wave metadata):
HELM_CHART=dummy-service IMAGE_TAG=production-abc123 DEPLOY_STRATEGY=gradual \
DRY_RUN=true GH_TOKEN=x GH_APPROVE_TOKEN=y python -m helm_image_updater
```
Expected: 4 "[DRY RUN] Would create PR" lines (waves 0–3) each printing its `release:id:* / release:wave:N / deploy:gradual` labels and `Auto-merge: False`.

- [ ] **Step 3: Commit any doc updates**

```bash
git add -A
git commit -m "docs: note PR-A implemented"
```

- [ ] **Step 4: Supersede PR #19 (requires confirmation — do not run without asking the user)**

```bash
gh pr close 19 --repo keboola/helm-image-updater \
  --comment "Superseded by ST-4034 DEPLOY_STRATEGY work (deploy-strategy-promoter-integration.md)."
```

---

## Self-review notes (gaps intentionally deferred to PR-B / out of scope)

- **`rollout_wave` data** for real production stacks is authored in kbc-stacks (user-owned). The **testing mocks' `stack-metadata.yaml` files** (currently absent — verified 0 present) are created in **PR-B**, alongside the gradual soak-progression integration test.
- **DESIGN.md note** (drop "HIU is not modified" MVP caveat) is a `release-promoter`-repo change, tracked separately.
- **Branch-protection / promoter merge authorization** is an external prerequisite surfaced by PR-B's test, not code here.
