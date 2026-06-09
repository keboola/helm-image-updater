# Release-manifest emission (HIU) + harness alignment (HIU-testing) + live e2e — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Strict TDD per repo: write the failing test → run red → minimal code → run green → commit.

**Goal:** Make `helm-image-updater` emit the release-promoter's **wave-0 body JSON manifest** (the new grouping contract that replaced the `release:id` label), update `helm-image-updater-testing`'s wave-drive harness to validate that contract, and run a live end-to-end test driving the promoter image `dev-ST-4034-1` through a real gradual rollout.

**Architecture:** The release-promoter migrated release grouping from an unbounded `release:id` label to a **JSON manifest embedded in the wave-0 "anchor PR" body** (release-promoter `src/core/interpret/manifest.ts`). HIU already opens 4 unmerged wave PRs (0..3) labelled `release:wave:N` + `deploy:<strategy>`; the only missing piece is writing the manifest. Because the manifest's `waves` map needs the **assigned PR numbers** (known only after creation), HIU adopts a **create-then-patch** flow: create all wave PRs → collect `{wave → PR#}` → patch the wave-0 PR body with the manifest. `release:id` is removed entirely; HIU's duplicate-fan-out guard is re-implemented to detect an open release by parsing the `instanceId` from existing `release:wave:0` anchor bodies.

**Tech Stack:** HIU = Python 3 (functional-core/imperative-shell, `pytest`, PyGithub, `ruamel.yaml`). HIU-testing = GitHub Actions workflow + Node `@actions/github` harness scripts; validated by the live `test-suite.yaml` run, not unit tests.

---

## Contract reference (what the promoter consumes — do NOT drift)

Source of truth: release-promoter `src/core/interpret/manifest.ts` (`extractManifest`/`isManifestV1`) + `src/core/interpret/shape.ts` (`wellFormed`).

**Manifest** — the promoter scans the wave-0 anchor PR body for the **first** ` ```json ` fence whose parsed object satisfies, exactly:

```json
{
  "manifestVersion": "v1",
  "instanceId": "<app>-<sourceSha[:12]>",
  "displayName": "<human label, may contain spaces/@>",
  "app": "<chart>",
  "anchorWave": 0,
  "waves": { "0": <pr#>, "1": <pr#>, "2": <pr#>, "3": <pr#> },
  "sourceSha": "<optional>",
  "sourcePr": "<optional>"
}
```

Hard validation (promoter rejects → `null`/`¬wellFormed` → `Conflicted`, never merged):
- `manifestVersion === "v1"`.
- `instanceId`: non-empty string, matches `/^[^\s:@]+$/` (no whitespace, no `:`, no `@`).
- `displayName`: any string (may be empty).
- `app`: non-empty string.
- `anchorWave`: exactly the number `0`.
- `waves`: non-array object, non-empty, key `"0"` present, every key matches `/^(0|[1-9]\d*)$/`, every value a **positive integer**, all values **distinct**.
- `sourceSha`/`sourcePr`: if present, strings.
- Fence regex is ` ```json\r?\n(.*?)``` ` (so emit ` ```json ` + `\n` + JSON + `\n` + ` ``` `).

`wellFormed` additionally cross-validates the loaded PRs (so HIU must keep producing these, which it already does, plus the manifest):
- The PR set matches `waves` **exactly** (one PR per key, `pr.wave === key`, each carries `release:wave:<key>`), wave keys contiguous from 0.
- `anchorPr === waves["0"]` (anchor = the wave-0 PR).
- All PRs derive **one** app; that app **equals** `manifest.app`.
- Every changed path on every wave PR ends with `/tag.yaml` (**`tag.yaml`-only** — see Deferred Follow-up).

---

## Scope & decisions

**In scope:** manifest emission in HIU; removal of `release:id`; harness validation of the manifest + completion-on-anchor; a live gradual-rollout e2e using promoter image `dev-ST-4034-1`.

**Decisions (locked by the user 2026-06-09):**
1. **`tag.yaml`-only / override-removal:** *Defer the production fix.* Do **not** change HIU's override-removal logic in this plan. Instead, ensure the wave-drive **test fixtures** carry no `argocdApplication.appManifestsRevision` override (so wave PRs are `tag.yaml`-only and the e2e is green). The latent production bug — a real overridden stack would put `values.yaml` in a wave PR → `¬wellFormed` → `Conflicted` — is recorded in **Deferred Follow-up** and must be tracked separately.
2. **`release:id`:** *Remove entirely.* Drop the `release:id:<id>` label from wave PRs and re-implement HIU's duplicate-fan-out guard to detect an existing open release by parsing the `instanceId` from each open `release:wave:0` anchor PR body.

**Out of scope (deferred, do not start here):** the production override-removal/`tag.yaml`-only fix; cryptographic manifest signing; any change to the promoter itself (already shipped, image `dev-ST-4034-1`).

**Revision 1 — Codex (`gpt-5.5`) review hardening (2026-06-09).** Folded in: F1 machine-safe `instanceId` sanitization (A1); F2/F5 full `isManifestV1` mirror in Python + JS (A1, B1); F3 the executor refuses to emit a **partial** manifest — every declared wave PR number must be present or the manifest is withheld and execution fails (A5); F4 `waitForWavePrs`/`findWavePrs` now wait for + re-fetch the wave-0 manifest (covers the create-then-patch race for the 2nd app in parallel/serialize) (B1); F6 deterministic `instanceId` fallback (`sha256(app\0image_tag)`) instead of a random UUID, so reruns are detectable without pipeline metadata (A1); F7 B3 corrected to a doc-only comment fix (cleanup already scans all waves); F8 `release:id` doc/comment cleanup broadened (A6, B3); F9 B4 audit target tightened to the overlaid working tree. Known accepted limitation: a manifest-less anchor exists only inside HIU's sub-second create-then-patch window — an HIU crash *between* creating wave-0 and patching its manifest could let a rerun duplicate (the promoter's grace window + duplicate-`instanceId` guard are the backstops).

**Scope-check note:** this spans two repos. Each part is independently testable — Part A by `pytest` in `helm-image-updater`, Part B by the live workflow in `helm-image-updater-testing`, Part C ties them together. Commits land in their respective repos; do not mix.

---

## File structure

### `helm-image-updater` (Part A)
- **Create** `helm_image_updater/manifest.py` — pure manifest helpers (`compute_instance_id`, `build_manifest`, `manifest_block`, `extract_instance_id`, `MANIFEST_HEADING`). Mirrors release-promoter `interpret/manifest.ts`. No I/O.
- **Create** `tests/test_manifest.py` — unit tests for the pure helpers.
- **Modify** `helm_image_updater/models.py` — add `PRPlan.wave_number: Optional[int]` and `UpdatePlan.manifest_context: Optional[Dict[str, Any]]`.
- **Modify** `helm_image_updater/io_layer.py` — add `update_pull_request_body(pr_number, body)` and `find_open_release_anchors() -> List[Tuple[int, str]]`.
- **Modify** `helm_image_updater/plan_builder.py` — drop `release:id` from wave labels/group; add `_build_manifest_context(plan)`; rewrite `_guard_release_not_already_open(instance_id, io_layer)`; set `wave_number` in `_create_pr_plan`; wire `manifest_context` + the new guard in `prepare_plan`.
- **Modify** `helm_image_updater/plan_executor.py` — collect `{wave → PR#}` during creation; after the loop, build the manifest and patch the wave-0 anchor body.
- **Modify** `tests/test_wave_grouping.py`, `tests/test_io_layer.py` (and any test asserting `release:id`) — to the new contract.

### `helm-image-updater-testing` (Part B)
- **Modify** `.github/scripts/test-suite/promoter-drive-lib.js` — `findWavePrs`: drop the `release:id` assertions; parse + validate the wave-0 body manifest; assert its `waves` map matches the discovered PR numbers.
- **Modify** `.github/scripts/test-suite/promoter-drive.js`, `promoter-drive-parallel.js`, `promoter-drive-serialize.js`, `promoter-drive-failure.js` — check `promoter:complete`/absence on the **wave-0 anchor** (`byWave[0]`) instead of the last wave (`byWave[3]`).
- **Modify** `.github/scripts/test-suite/cleanup.js` — seed discovery from `release:wave:0` (the anchor) while still scrubbing legacy `release:id:*` labels.
- **Verify/Modify** wave-scenario stack fixtures — ensure no `appManifestsRevision` override (Decision 1).

### Live e2e (Part C)
- Workflow `helm-image-updater-testing/.github/workflows/test-suite.yaml` dispatched with `helm-image-updater-branch=<HIU branch>`, `promoter-image-tag=dev-ST-4034-1`, `promoter-scenarios-only=true`.

---

# Part A — `helm-image-updater`: emit the manifest

> Work on a branch off the current `ST-4034-deploy-strategy-wave`. Run tests with `pytest` from the repo root. Commit per task.

### Task A1: Pure manifest helpers (`manifest.py`)

**Files:**
- Create: `helm_image_updater/manifest.py`
- Test: `tests/test_manifest.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_manifest.py
import json
import re
import pytest
from helm_image_updater.manifest import (
    compute_instance_id, build_manifest, manifest_block, extract_instance_id, MANIFEST_HEADING,
)

# Mirror the promoter's regexes so we test against the REAL acceptance criteria.
INSTANCE_ID_RE = re.compile(r"^[^\s:@]+$")
JSON_FENCE_RE = re.compile(r"```json\r?\n(.*?)```", re.DOTALL)


def test_compute_instance_id_from_sha():
    assert compute_instance_id("connection", "abcdef0123456789", "t") == "connection-abcdef012345"


def test_compute_instance_id_is_machine_safe_even_for_unsafe_app():
    # An app name with forbidden chars (defensive) must still yield a valid instanceId.
    iid = compute_instance_id("my app:weird@chart", "deadBEEF00112233", "t")
    assert INSTANCE_ID_RE.match(iid)


@pytest.mark.parametrize("sha", [None, "", "  ", "Unknown", "unknown"])
def test_compute_instance_id_deterministic_fallback_when_no_sha(sha):
    iid = compute_instance_id("connection", sha, "prod-123")
    assert iid.startswith("connection-") and len(iid) > len("connection-")
    assert INSTANCE_ID_RE.match(iid)
    # Deterministic: same (app, tag) → same id (so reruns are detectable, F6).
    assert iid == compute_instance_id("connection", None, "prod-123")
    # Different tag → different id (unique per fan-out).
    assert iid != compute_instance_id("connection", None, "prod-999")


def test_build_manifest_shape():
    m = build_manifest(
        app="connection", instance_id="connection-abc", display_name="connection@prod",
        waves={0: 10, 1: 11, 2: 12, 3: 13}, source_sha="abc", source_pr="https://x/pull/9",
    )
    assert m["manifestVersion"] == "v1"
    assert m["anchorWave"] == 0
    assert m["app"] == "connection"
    assert m["instanceId"] == "connection-abc"
    assert m["displayName"] == "connection@prod"
    assert m["waves"] == {"0": 10, "1": 11, "2": 12, "3": 13}  # int wave keys → string keys
    assert m["sourceSha"] == "abc" and m["sourcePr"] == "https://x/pull/9"


def test_build_manifest_omits_absent_optional_source_fields():
    m = build_manifest(app="a", instance_id="a-1", display_name="d", waves={0: 1})
    assert "sourceSha" not in m and "sourcePr" not in m


def test_manifest_block_is_extractable_by_promoter_regex():
    m = build_manifest(app="connection", instance_id="connection-abc", display_name="c@p",
                       waves={0: 10, 1: 11, 2: 12, 3: 13})
    block = manifest_block(m)
    assert MANIFEST_HEADING in block
    fences = JSON_FENCE_RE.findall(block)
    assert len(fences) == 1
    assert json.loads(fences[0]) == m  # round-trips to the exact object


def test_extract_instance_id_reads_first_valid_v1_manifest():
    body = "intro\n\n" + manifest_block(build_manifest(
        app="connection", instance_id="connection-abc", display_name="c", waves={0: 10}))
    assert extract_instance_id(body) == "connection-abc"


@pytest.mark.parametrize("body", [
    None, "", "no fence here",
    "```json\n{\"manifestVersion\": \"v2\"}\n```",          # wrong version
    "```json\n{not json}\n```",                                 # unparseable
    "```json\n{\"manifestVersion\": \"v1\"}\n```",            # missing instanceId
])
def test_extract_instance_id_returns_none_on_bad_body(body):
    assert extract_instance_id(body) is None
```

- [ ] **Step 2: Run red** — `pytest tests/test_manifest.py -v` → FAIL (`ModuleNotFoundError: helm_image_updater.manifest`).

- [ ] **Step 3: Implement `helm_image_updater/manifest.py`**

```python
"""Pure helpers for the release manifest embedded in the wave-0 anchor PR body.

The release-promoter groups a release by a JSON manifest in the wave-0 PR body
(NOT by a release:id label). This module builds that manifest, renders the
markdown block, and extracts an instanceId from an existing body for HIU's
idempotency guard. No I/O.

Mirrors release-promoter src/core/interpret/manifest.ts (extractManifest /
isManifestV1) — keep in sync if the promoter contract changes.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, Optional

MANIFEST_HEADING = "## ⚠️ Release manifest — DO NOT EDIT (machine-read by release-promoter)"

# Promoter's INSTANCE_ID_RE: non-empty, no whitespace / ':' / '@'.
_INSTANCE_ID_RE = re.compile(r"^[^\s:@]+$")
# Promoter's wave-key regex: non-negative integer string.
_WAVE_KEY_RE = re.compile(r"^(0|[1-9]\d*)$")
# Promoter's JSON_FENCE_RE.
_JSON_FENCE_RE = re.compile(r"```json\r?\n(.*?)```", re.DOTALL)
# Chars forbidden in an instanceId (the complement of INSTANCE_ID_RE's class).
_UNSAFE_RE = re.compile(r"[\s:@]")


def _machine_safe(app: str) -> str:
    """Coerce an app/chart name into the promoter's instanceId charset (no ws/:/@).
    Real kbc-stacks chart names are already safe; this is a defensive guarantee so a
    stray char can never produce a manifest the promoter would reject (F1)."""
    return _UNSAFE_RE.sub("-", app)


def compute_instance_id(app: str, source_sha: Optional[str], image_tag: str) -> str:
    """Machine-safe, DETERMINISTIC id. '<app>-<sha[:12]>' when a real source SHA is
    available; otherwise '<app>-<sha256(app\\0image_tag)[:12]>' — NOT a random UUID — so a
    re-run of the same (app, image_tag) yields the SAME id and HIU's idempotency guard still
    detects a duplicate fan-out when pipeline metadata is absent (the test harness passes no
    METADATA, F6). Unique per fan-out: a different tag → a different id; the promoter's
    duplicate-instanceId guard is the backstop for a genuine same-tag collision."""
    safe = _machine_safe(app)
    sha = (source_sha or "").strip()
    if sha and sha.lower() != "unknown":
        return f"{safe}-{sha[:12]}"
    digest = hashlib.sha256(f"{app}\0{image_tag}".encode()).hexdigest()[:12]
    return f"{safe}-{digest}"


def build_manifest(
    *,
    app: str,
    instance_id: str,
    display_name: str,
    waves: Dict[int, int],
    source_sha: Optional[str] = None,
    source_pr: Optional[str] = None,
) -> Dict[str, Any]:
    """Build the v1 manifest object. `waves` maps wave number -> PR number."""
    manifest: Dict[str, Any] = {
        "manifestVersion": "v1",
        "instanceId": instance_id,
        "displayName": display_name,
        "app": app,
        "anchorWave": 0,
        "waves": {str(w): n for w, n in sorted(waves.items())},
    }
    if source_sha:
        manifest["sourceSha"] = source_sha
    if source_pr:
        manifest["sourcePr"] = source_pr
    return manifest


def manifest_block(manifest: Dict[str, Any]) -> str:
    """Render the markdown block (heading + ```json fence) to append to the wave-0
    PR body. The fence shape matches the promoter's extractManifest regex."""
    body = json.dumps(manifest, indent=2, ensure_ascii=False)
    return f"{MANIFEST_HEADING}\n\n```json\n{body}\n```"


def is_manifest_v1(x: Any) -> bool:
    """Full mirror of release-promoter isManifestV1 (manifest.ts) so HIU validates EXACTLY
    what the promoter would accept (F2). Never throws."""
    if not isinstance(x, dict):
        return False
    if x.get("manifestVersion") != "v1":
        return False
    iid = x.get("instanceId")
    if not isinstance(iid, str) or not iid or not _INSTANCE_ID_RE.match(iid):
        return False
    if not isinstance(x.get("displayName"), str):
        return False
    app = x.get("app")
    if not isinstance(app, str) or not app:
        return False
    if x.get("anchorWave") != 0:
        return False
    waves = x.get("waves")
    if not isinstance(waves, dict) or not waves or "0" not in waves:
        return False
    seen = set()
    for key, val in waves.items():
        if not _WAVE_KEY_RE.match(key):
            return False
        # bool is an int subclass — exclude it explicitly.
        if not isinstance(val, int) or isinstance(val, bool) or val <= 0 or val in seen:
            return False
        seen.add(val)
    for opt in ("sourceSha", "sourcePr"):
        if opt in x and not isinstance(x[opt], str):
            return False
    return True


def extract_instance_id(body: Optional[str]) -> Optional[str]:
    """Return the instanceId of the first FULLY-VALID v1 manifest in `body`, else None.
    Used by HIU's idempotency guard to detect a duplicate open release."""
    if not body:
        return None
    for m in _JSON_FENCE_RE.finditer(body):
        try:
            parsed = json.loads(m.group(1))
        except (ValueError, TypeError):
            continue
        if is_manifest_v1(parsed):
            return parsed["instanceId"]
    return None
```

- [ ] **Step 4: Run green** — `pytest tests/test_manifest.py -v` → PASS.
- [ ] **Step 5: Commit** — `git add helm_image_updater/manifest.py tests/test_manifest.py && git commit -m "feat(manifest): pure helpers to build/extract the release manifest"`

---

### Task A2: io_layer — patch a PR body + list open anchors

**Files:**
- Modify: `helm_image_updater/io_layer.py` (add two methods after `find_prs_by_label`, ~line 389)
- Test: `tests/test_io_layer.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_io_layer.py  (add)
from unittest.mock import MagicMock
from helm_image_updater.io_layer import IOLayer


def _io(github_repo, dry_run=False):
    return IOLayer(repo=MagicMock(), github_repo=github_repo, dry_run=dry_run,
                   approve_github_repo=MagicMock())


def test_update_pull_request_body_edits_pr():
    gh = MagicMock()
    pr = MagicMock()
    gh.get_pull.return_value = pr
    _io(gh).update_pull_request_body(42, "new body")
    gh.get_pull.assert_called_once_with(42)
    pr.edit.assert_called_once_with(body="new body")


def test_update_pull_request_body_noop_in_dry_run():
    gh = MagicMock()
    _io(gh, dry_run=True).update_pull_request_body(42, "x")
    gh.get_pull.assert_not_called()


def test_find_open_release_anchors_returns_number_and_body():
    gh = MagicMock()
    issue = MagicMock(); issue.number = 7; issue.pull_request = object()
    non_pr = MagicMock(); non_pr.pull_request = None
    gh.get_issues.return_value = [issue, non_pr]
    pr = MagicMock(); pr.body = "BODY"
    gh.get_pull.return_value = pr
    anchors = _io(gh).find_open_release_anchors()
    gh.get_issues.assert_called_once_with(state="open", labels=["release:wave:0"])
    assert anchors == [(7, "BODY")]
```

- [ ] **Step 2: Run red** — `pytest tests/test_io_layer.py -k "release_anchors or update_pull_request_body" -v` → FAIL (`AttributeError`).

- [ ] **Step 3: Implement (append to `io_layer.py` after `find_prs_by_label`)**

```python
    def update_pull_request_body(self, pr_number: int, body: str) -> None:
        """Patch a PR's body in place (used to inject the release manifest into the
        wave-0 anchor once all wave PR numbers are known)."""
        if self.dry_run:
            print(f"[DRY RUN] Would update body of PR #{pr_number}")
            return
        pr = self.github_repo.get_pull(pr_number)
        pr.edit(body=body)
        print(f"📝 Updated PR #{pr_number} body (release manifest injected)")

    def find_open_release_anchors(self) -> List[Tuple[int, str]]:
        """Return (number, body) for every OPEN wave-0 anchor PR (label release:wave:0).
        Used by the idempotency guard to detect an existing open release by instanceId."""
        anchors: List[Tuple[int, str]] = []
        for issue in self.github_repo.get_issues(state="open", labels=["release:wave:0"]):
            if issue.pull_request is None:
                continue
            pr = self.github_repo.get_pull(issue.number)
            anchors.append((issue.number, pr.body or ""))
        return anchors
```

Ensure `Tuple` is imported at the top of `io_layer.py`: `from typing import Any, List, Optional, Tuple` (add `Tuple` if absent).

- [ ] **Step 4: Run green** — `pytest tests/test_io_layer.py -v` → PASS.
- [ ] **Step 5: Commit** — `git add helm_image_updater/io_layer.py tests/test_io_layer.py && git commit -m "feat(io): update PR body + list open release:wave:0 anchors"`

---

### Task A3: models — carry `wave_number` and `manifest_context`

**Files:**
- Modify: `helm_image_updater/models.py:53-63` (PRPlan), `:66-87` (UpdatePlan)
- Test: `tests/test_models.py` (create if absent)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_models.py  (add)
from helm_image_updater.models import PRPlan, UpdatePlan, UpdateStrategy


def test_prplan_wave_number_defaults_none_and_is_settable():
    p = PRPlan(branch_name="b", pr_title="t", pr_body="x", base_branch="main",
               auto_merge=False, files_to_commit=[], commit_message="c")
    assert p.wave_number is None
    p2 = PRPlan(branch_name="b", pr_title="t", pr_body="x", base_branch="main",
                auto_merge=False, files_to_commit=[], commit_message="c", wave_number=0)
    assert p2.wave_number == 0


def test_updateplan_manifest_context_defaults_none():
    u = UpdatePlan(strategy=UpdateStrategy.PRODUCTION, helm_chart="connection", image_tag="t")
    assert u.manifest_context is None
```

- [ ] **Step 2: Run red** — `pytest tests/test_models.py -v` → FAIL (`TypeError: unexpected keyword 'wave_number'`).

- [ ] **Step 3: Implement**

In `PRPlan` (after `labels: List[str] = ...`):
```python
    wave_number: Optional[int] = None  # set only for pr_type == 'wave'
```
In `UpdatePlan` (after `metadata: Dict[str, Any] = ...`):
```python
    manifest_context: Optional[Dict[str, Any]] = None  # {app, instance_id, display_name, source_sha, source_pr}; wave mode only
```

- [ ] **Step 4: Run green** — `pytest tests/test_models.py -v` → PASS.
- [ ] **Step 5: Commit** — `git add helm_image_updater/models.py tests/test_models.py && git commit -m "feat(models): PRPlan.wave_number + UpdatePlan.manifest_context"`

---

### Task A4: plan_builder — drop `release:id`, build manifest context, instanceId-based idempotency guard

**Files:**
- Modify: `helm_image_updater/plan_builder.py` — imports (`:14`), `_group_changes_by_wave` (`:502-538`), `_guard_release_not_already_open` (`:541-553`), `_create_pr_plan` (`:649-658`), `prepare_plan` (`:88-93`); add `_build_manifest_context`.
- Test: `tests/test_wave_grouping.py` (update existing + add).

- [ ] **Step 1: Update the failing tests (new contract)**

In `tests/test_wave_grouping.py`, replace the `release:id` assertions. The wave labels are now exactly `[release:wave:N, deploy:<strategy>]` (no `release:id`):

```python
# Each wave group carries exactly its wave label + the deploy label (no release:id).
def test_wave_groups_have_wave_and_deploy_labels_only(...):  # adapt existing fixture
    groups = _group_changes_by_wave(stack_changes, plan, config, io_layer)
    for g in groups:
        assert g['labels'] == [f"release:wave:{g['wave_number']}", "deploy:gradual"]
        assert not any(l.startswith('release:id:') for l in g['labels'])
        assert 'release_id' not in g
```

Replace the idempotency test to use the new guard + `find_open_release_anchors`:

```python
from unittest.mock import MagicMock
from helm_image_updater.plan_builder import _guard_release_not_already_open
from helm_image_updater.manifest import manifest_block, build_manifest


def test_guard_raises_when_instance_id_already_open():
    io = MagicMock()
    body = manifest_block(build_manifest(app="connection", instance_id="connection-abc",
                                         display_name="c", waves={0: 10, 1: 11, 2: 12, 3: 13}))
    io.find_open_release_anchors.return_value = [(10, body)]
    with pytest.raises(RuntimeError, match="already has an open anchor"):
        _guard_release_not_already_open("connection-abc", io)


def test_guard_passes_when_no_matching_open_release():
    io = MagicMock()
    io.find_open_release_anchors.return_value = []
    _guard_release_not_already_open("connection-abc", io)  # no raise
```

- [ ] **Step 2: Run red** — `pytest tests/test_wave_grouping.py -v` → FAIL.

- [ ] **Step 3: Implement plan_builder changes**

(a) Imports at `:14` — drop the retired helpers:
```python
from .wave_planning import wave_label, deploy_label, resolve_wave
from .manifest import compute_instance_id
```
(Delete `compute_release_id, release_id_label` from the import; they are no longer used here.)

(b) `_group_changes_by_wave` (`:502-538`) — remove `release_id` and the `release:id` label:
```python
def _group_changes_by_wave(stack_changes, plan, config, io_layer):
    """Group changes into one PR per rollout wave (0..3) for promoter consumption."""
    deploy_lbl = deploy_label(config.deploy_strategy)

    stack_changes = [sc for sc in stack_changes if not sc['stack'].endswith('-e2e')]

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
            'labels': [wave_label(wave), deploy_lbl],
        })
    return groups
```

(c) Replace `_guard_release_not_already_open` (`:541-553`):
```python
def _guard_release_not_already_open(instance_id: str, io_layer: IOLayer) -> None:
    """Fail loudly if an open release with this instanceId already exists (re-run safety).

    Grouping moved from the release:id label to the wave-0 body manifest, so we detect a
    duplicate by parsing the instanceId out of each OPEN release:wave:0 anchor PR body.
    A second fan-out for the same instanceId would give the promoter a duplicate release.
    """
    from .manifest import extract_instance_id
    for number, body in io_layer.find_open_release_anchors():
        if extract_instance_id(body) == instance_id:
            raise RuntimeError(
                f"Release '{instance_id}' already has an open anchor PR #{number}. "
                f"Refusing to create duplicate wave PRs. Close/finish the existing release first."
            )
```

(d) Add `_build_manifest_context` (place near `_group_changes_by_wave`):
```python
def _build_manifest_context(plan: UpdatePlan) -> Dict[str, Any]:
    """Compute the wave-0 manifest's identity fields from the plan + pipeline metadata."""
    source = (plan.metadata or {}).get("source", {})
    source_sha = source.get("sha")
    source_pr = source.get("pr_url")
    return {
        "app": plan.helm_chart,
        "instance_id": compute_instance_id(plan.helm_chart, source_sha, plan.image_tag),
        "display_name": f"{plan.helm_chart}@{plan.image_tag}",
        "source_sha": source_sha if (source_sha and str(source_sha).lower() != "unknown") else None,
        "source_pr": source_pr or None,
    }
```

(e) `_create_pr_plan` — pass `wave_number` through (`:649-658`):
```python
    return PRPlan(
        branch_name=branch_name,
        pr_title=pr_title,
        pr_body=pr_body,
        base_branch=pr_group['base_branch'],
        auto_merge=auto_merge,
        files_to_commit=files_to_commit,
        commit_message=commit_message,
        labels=pr_group.get('labels', []),
        wave_number=pr_group.get('wave_number'),
    )
```

(f) `prepare_plan` (`:88-93`) — compute context + new guard:
```python
    # Group changes into PRs
    pr_groups = _group_changes_for_prs(stack_changes, plan, config, io_layer)

    # Wave mode: derive the manifest identity, then guard against a duplicate fan-out.
    if config.deploy_strategy.is_wave and pr_groups:
        plan.manifest_context = _build_manifest_context(plan)
        if not config.dry_run:
            _guard_release_not_already_open(plan.manifest_context["instance_id"], io_layer)
```

- [ ] **Step 4: Run green** — `pytest tests/test_wave_grouping.py -v` → PASS.
- [ ] **Step 5: Commit** — `git add helm_image_updater/plan_builder.py tests/test_wave_grouping.py && git commit -m "feat(wave): drop release:id; instanceId-based idempotency + manifest context"`

---

### Task A5: executor — patch the wave-0 anchor with the manifest

**Files:**
- Modify: `helm_image_updater/plan_executor.py` (`_execute_pr_plans`, `:69-103`; add `_patch_anchor_manifest` + a PR-number parse helper)
- Test: `tests/test_plan_executor.py` (create if absent)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_plan_executor.py  (add)
import json, re
from unittest.mock import MagicMock
from helm_image_updater.models import UpdatePlan, PRPlan, FileChange, UpdateStrategy
from helm_image_updater.plan_executor import execute_plan

JSON_FENCE_RE = re.compile(r"```json\r?\n(.*?)```", re.DOTALL)


def _wave_plan():
    plan = UpdatePlan(strategy=UpdateStrategy.PRODUCTION, helm_chart="connection", image_tag="t")
    plan.manifest_context = {"app": "connection", "instance_id": "connection-abc",
                             "display_name": "connection@t", "source_sha": "abc", "source_pr": None}
    for w in range(4):
        fc = FileChange(file_path=f"stack{w}/connection/tag.yaml", old_content="a", new_content="b",
                        change_description="d")
        plan.file_changes.append(fc)
        plan.pr_plans.append(PRPlan(branch_name=f"connection-wave{w}-t-xxxx", pr_title=f"w{w}",
                                    pr_body=f"BODY{w}", base_branch="main", auto_merge=False,
                                    files_to_commit=[fc.file_path], commit_message="c",
                                    labels=[f"release:wave:{w}", "deploy:gradual"], wave_number=w))
    return plan


def test_executor_patches_wave0_anchor_with_manifest():
    plan = _wave_plan()
    io = MagicMock()
    # create_branch_commit_and_pr returns the PR URL; wave w -> PR number 10+w.
    io.create_branch_commit_and_pr.side_effect = [
        f"https://github.com/keboola/kbc-stacks/pull/{10 + w}" for w in range(4)
    ]
    execute_plan(plan, io)

    io.update_pull_request_body.assert_called_once()
    (anchor_num, new_body), _ = io.update_pull_request_body.call_args
    assert anchor_num == 10  # wave-0 PR number
    fences = JSON_FENCE_RE.findall(new_body)
    manifest = json.loads(fences[0])
    assert manifest["instanceId"] == "connection-abc"
    assert manifest["app"] == "connection"
    assert manifest["anchorWave"] == 0
    assert manifest["waves"] == {"0": 10, "1": 11, "2": 12, "3": 13}
    assert new_body.startswith("BODY0")  # appended to the original wave-0 body


def test_executor_no_patch_when_not_wave_mode():
    plan = UpdatePlan(strategy=UpdateStrategy.PRODUCTION, helm_chart="connection", image_tag="t")
    fc = FileChange(file_path="s/connection/tag.yaml", old_content="a", new_content="b", change_description="d")
    plan.file_changes.append(fc)
    plan.pr_plans.append(PRPlan(branch_name="b", pr_title="t", pr_body="x", base_branch="main",
                                auto_merge=False, files_to_commit=[fc.file_path], commit_message="c"))
    io = MagicMock(); io.create_branch_commit_and_pr.return_value = "https://github.com/o/r/pull/5"
    execute_plan(plan, io)
    io.update_pull_request_body.assert_not_called()


def test_executor_withholds_manifest_on_partial_creation():
    # F3: if any wave PR fails to create (returns None), the manifest must NOT be patched —
    # a partial manifest would orphan the un-listed wave PRs.
    plan = _wave_plan()
    io = MagicMock()
    io.create_branch_commit_and_pr.side_effect = [
        "https://github.com/keboola/kbc-stacks/pull/10",
        "https://github.com/keboola/kbc-stacks/pull/11",
        None,  # wave 2 creation failed
        "https://github.com/keboola/kbc-stacks/pull/13",
    ]
    result = execute_plan(plan, io)
    io.update_pull_request_body.assert_not_called()
    assert result.success is False
```

- [ ] **Step 2: Run red** — `pytest tests/test_plan_executor.py -v` → FAIL (no manifest patch).

- [ ] **Step 3: Implement**

Add imports at the top of `plan_executor.py`:
```python
import re
from .manifest import build_manifest, manifest_block

_PR_NUM_RE = re.compile(r"/pull/(\d+)")


def _pr_number_from_url(url: str) -> Optional[int]:
    m = _PR_NUM_RE.search(url or "")
    return int(m.group(1)) if m else None
```

Rewrite `_execute_pr_plans` to collect wave numbers and patch after the loop:
```python
def _execute_pr_plans(plan: UpdatePlan, io_layer: IOLayer, result: ExecutionResult):
    """Create all pull requests; then (wave mode) patch the wave-0 anchor manifest."""
    wave_pr_numbers: Dict[int, int] = {}   # wave -> PR number
    wave0_body: Optional[str] = None       # the wave-0 PR's created body

    for pr_plan in plan.pr_plans:
        if plan.dry_run:
            print(f"[DRY RUN] Would create PR: {pr_plan.pr_title}")
            print(f"  Branch: {pr_plan.branch_name}")
            print(f"  Base: {pr_plan.base_branch}")
            print(f"  Auto-merge: {pr_plan.auto_merge}")
            print(f"  Files: {', '.join(pr_plan.files_to_commit)}")
            if pr_plan.labels:
                print(f"  Labels: {', '.join(pr_plan.labels)}")
            continue

        relevant_file_changes = [fc for fc in plan.file_changes
                                 if fc.file_path in pr_plan.files_to_commit]
        io_layer.write_file_changes(relevant_file_changes)

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

        if pr_url:
            result.pr_urls.append(pr_url)
            print(f"Created PR: {pr_plan.pr_title}")
            if pr_plan.wave_number is not None:
                num = _pr_number_from_url(pr_url)
                if num is not None:
                    wave_pr_numbers[pr_plan.wave_number] = num
                    if pr_plan.wave_number == 0:
                        wave0_body = pr_plan.pr_body
                else:
                    result.success = False
                    result.errors.append(
                        f"Could not parse PR number from URL '{pr_url}' for wave "
                        f"{pr_plan.wave_number}; manifest will be withheld (F3).")
        else:
            result.errors.append(f"Failed to create PR: {pr_plan.pr_title}")
            if pr_plan.wave_number is not None:
                result.success = False  # a missing wave PR → withhold the manifest (F3)

    if not plan.dry_run and plan.manifest_context and wave_pr_numbers:
        _patch_anchor_manifest(plan, io_layer, wave_pr_numbers, wave0_body, result)


def _patch_anchor_manifest(plan, io_layer, wave_pr_numbers, wave0_body, result):
    """Build the v1 manifest from the collected {wave -> PR#} and patch the wave-0 body.

    F3: refuse to emit a PARTIAL manifest. Every declared wave PR must have a parsed PR
    number, or we withhold the manifest entirely (a manifest with a subset of waves is
    structurally valid to the promoter — it would treat it as a shorter release and orphan
    the un-listed wave PRs)."""
    ctx = plan.manifest_context
    expected = {p.wave_number for p in plan.pr_plans if p.wave_number is not None}
    if set(wave_pr_numbers) != expected or wave0_body is None:
        missing = sorted(expected - set(wave_pr_numbers))
        result.success = False
        result.errors.append(
            f"Manifest NOT written: missing PR numbers for waves {missing} "
            f"(collected {sorted(wave_pr_numbers)}) or wave-0 body unavailable; refusing to "
            f"emit a partial manifest that would orphan wave PRs (F3)."
        )
        return
    anchor = wave_pr_numbers[0]
    manifest = build_manifest(
        app=ctx["app"], instance_id=ctx["instance_id"], display_name=ctx["display_name"],
        waves=wave_pr_numbers, source_sha=ctx.get("source_sha"), source_pr=ctx.get("source_pr"),
    )
    new_body = f"{wave0_body}\n\n{manifest_block(manifest)}"
    io_layer.update_pull_request_body(anchor, new_body)
    print(f"🧭 Release manifest written to wave-0 anchor PR #{anchor} "
          f"(instanceId={ctx['instance_id']}, waves={manifest['waves']})")
```

Ensure `Optional` and `Dict` are imported in `plan_executor.py` (`from typing import Dict, List, Optional`).

- [ ] **Step 4: Run green** — `pytest tests/test_plan_executor.py -v` → PASS.
- [ ] **Step 5: Commit** — `git add helm_image_updater/plan_executor.py tests/test_plan_executor.py && git commit -m "feat(executor): patch wave-0 anchor with the release manifest"`

---

### Task A6: full suite green + retire dead `release:id` code paths

**Files:** repo-wide.

- [ ] **Step 1:** Run the whole suite — `pytest -q`. Fix any test still asserting `release:id` on wave PRs (search: `grep -rn "release:id" tests/`). The only remaining `release:id` references should be (a) `wave_planning.compute_release_id`/`release_id_label` IF still imported anywhere — otherwise delete them and their tests, and (b) nothing in `plan_builder`.
- [ ] **Step 2:** `grep -rn "compute_release_id\|release_id_label\|release_id" helm_image_updater/` — confirm no live references remain in `plan_builder.py`. If `compute_release_id`/`release_id_label` are now unused, delete them from `wave_planning.py` and delete their unit tests; if any other module still uses them, leave them.
- [ ] **Step 2b (F8 — docs):** `grep -rn "release:id" README.md docs/` and update HIU docs that still describe `release:id` as the promoter grouping contract — `README.md` (~`:286`) and `docs/plans/2026-06-05-deploy-strategy-wave-pr-a.md` (the deploy-strategy spec, ~`:20-53`): state that the `release:id` label is **retired** and grouping moved to the wave-0 body manifest, or mark the old design doc superseded by this plan. Do not leave docs instructing the retired contract.
- [ ] **Step 3:** Run `pytest -q` → all green.
- [ ] **Step 4: Commit** — `git add -A && git commit -m "chore(wave): remove dead release:id helpers + docs; suite green on manifest contract"`

---

# Part B — `helm-image-updater-testing`: validate the manifest contract

> Work on a branch off the current `ST-4034-pr-b-gradual-test`. These are GitHub Actions harness scripts with **no unit-test runner** — they are validated by the live `test-suite.yaml` run in Part C. Keep edits minimal and reviewable; `node -c <file>` to syntax-check each. Commit per task.

### Task B1: `findWavePrs` validates the manifest, drops `release:id`

**Files:** Modify `.github/scripts/test-suite/promoter-drive-lib.js` (`findWavePrs`, `:115-143`).

- [ ] **Step 1:** Replace the `release:id` assertions (lines `:131-132` and the `release:id:*` clause at `:137`) with manifest validation. New `findWavePrs` body:

```js
/**
 * Discover THIS run's 4 wave PRs (matched by `matchToken` in the head branch), index by
 * wave number, and validate the release shape: exactly 4, no duplicate wave, contiguous
 * waves 0..3, each carrying release:wave:N + `deployLabel`, each starting open/unmerged,
 * AND a valid v1 release manifest in the wave-0 anchor body whose `waves` map matches the
 * discovered PR numbers (the grouping contract — release:id is retired). Returns { byWave, waves }.
 */
async function findWavePrs(github, owner, repo, { matchToken, deployLabel }, core, prefetchedAll = null) {
  const all = prefetchedAll ?? (await github.paginate(github.rest.pulls.list, { owner, repo, state: 'all', per_page: 100 }));
  const mine = all.filter((pr) => pr.head?.ref?.includes(matchToken) && labelNames(pr).some((n) => n.startsWith('release:wave:')));
  assert(mine.length === 4, core, `expected exactly 4 wave PRs for this run, found ${mine.length}: ${mine.map((p) => '#' + p.number)}`);
  const byWave = {};
  for (const pr of mine) {
    const wn = parseInt(labelNames(pr).find((n) => n.startsWith('release:wave:')).split(':')[2], 10);
    assert(byWave[wn] === undefined, core, `duplicate release:wave:${wn} PRs (#${byWave[wn] && byWave[wn].number} and #${pr.number})`);
    byWave[wn] = pr;
  }
  const waves = Object.keys(byWave).map(Number).sort((a, b) => a - b);
  assert(JSON.stringify(waves) === JSON.stringify([0, 1, 2, 3]), core, `expected wave PRs 0..3, got ${waves}`);
  for (const w of waves) {
    const ls = labelNames(byWave[w]);
    assert(ls.includes(`release:wave:${w}`) && ls.includes(deployLabel),
      core, `wave ${w} PR #${byWave[w].number} missing expected labels (want release:wave:${w} + ${deployLabel}): ${ls}`);
    assert(byWave[w].merged !== true && byWave[w].state === 'open', core, `wave ${w} PR must start open/unmerged`);
  }

  // Grouping contract: the wave-0 anchor body must carry a valid v1 manifest whose waves map
  // equals the discovered PR numbers (release:id is retired; the promoter groups by this).
  // Refetch the anchor via pulls.get — the pulls.list snapshot may predate HIU's manifest
  // patch (create-then-patch), so reading byWave[0].body from the list could miss it (F4).
  const anchorData = (await withRetry(() => github.rest.pulls.get({ owner, repo, pull_number: byWave[0].number }), `fetch anchor #${byWave[0].number}`)).data;
  const manifest = extractManifest(anchorData.body);
  assert(manifest, core, `wave-0 anchor PR #${byWave[0].number} has no valid v1 release manifest in its body`);
  assert(manifest.anchorWave === 0, core, `manifest anchorWave must be 0, got ${manifest.anchorWave}`);
  assert(manifest.waves['0'] === byWave[0].number, core, `manifest waves["0"] (${manifest.waves['0']}) must equal the wave-0 PR #${byWave[0].number}`);
  for (const w of waves) {
    assert(manifest.waves[String(w)] === byWave[w].number, core,
      `manifest waves["${w}"]=${manifest.waves[String(w)]} must equal discovered wave ${w} PR #${byWave[w].number}`);
  }
  core.info(`Found wave PRs: ${waves.map((w) => `w${w}=#${byWave[w].number}`).join(', ')} (instanceId=${manifest.instanceId})`);
  return { byWave, waves };
}
```

- [ ] **Step 2:** Add an `extractManifest` helper near the top of `promoter-drive-lib.js` (mirrors the promoter) and export it:

```js
// Mirror release-promoter src/core/interpret/manifest.ts EXACTLY (F5): first ```json fence
// whose object is a fully-valid v1 manifest. Returns the object or null. Keep in sync.
const _JSON_FENCE_RE = /```json\r?\n([\s\S]*?)```/g;
const _INSTANCE_ID_RE = /^[^\s:@]+$/;
const _WAVE_KEY_RE = /^(0|[1-9]\d*)$/;
function isManifestV1(o) {
  if (!o || typeof o !== 'object' || Array.isArray(o)) return false;
  if (o.manifestVersion !== 'v1') return false;
  if (typeof o.instanceId !== 'string' || !o.instanceId || !_INSTANCE_ID_RE.test(o.instanceId)) return false;
  if (typeof o.displayName !== 'string') return false;
  if (typeof o.app !== 'string' || !o.app) return false;
  if (o.anchorWave !== 0) return false;
  const w = o.waves;
  if (!w || typeof w !== 'object' || Array.isArray(w)) return false;
  const keys = Object.keys(w);
  if (keys.length === 0 || !Object.prototype.hasOwnProperty.call(w, '0')) return false;
  const seen = new Set();
  for (const k of keys) {
    if (!_WAVE_KEY_RE.test(k)) return false;
    const v = w[k];
    if (!Number.isInteger(v) || v <= 0 || seen.has(v)) return false;
    seen.add(v);
  }
  if ('sourceSha' in o && typeof o.sourceSha !== 'string') return false;
  if ('sourcePr' in o && typeof o.sourcePr !== 'string') return false;
  return true;
}
function extractManifest(body) {
  if (!body) return null;
  _JSON_FENCE_RE.lastIndex = 0;
  let m;
  while ((m = _JSON_FENCE_RE.exec(body)) !== null) {
    let o;
    try { o = JSON.parse(m[1]); } catch { continue; }
    if (isManifestV1(o)) return o;
  }
  return null;
}
```

Add `extractManifest` (and `isManifestV1`) to the `module.exports` list at the bottom.

- [ ] **Step 2b (F4): gate `waitForWavePrs` on the manifest being patched in.** Today it returns as soon as 4 labelled wave PRs exist and reuses that snapshot. Because HIU creates the 4 PRs and patches the wave-0 manifest *afterwards* (create-then-patch), and the parallel/serialize harnesses dispatch the 2nd app's HIU **inside** the drive (no "wait for workflow" step), the snapshot can predate the patch and `findWavePrs` would throw on a *valid* run. Make the poll also require a valid wave-0 manifest whose `waves` match the discovered PR numbers before returning:

```js
async function waitForWavePrs(github, owner, repo, { matchToken, deployLabel }, core, { tries = 36, delayMs = 5000 } = {}) {
  for (let i = 1; i <= tries; i++) {
    const all = await github.paginate(github.rest.pulls.list, { owner, repo, state: 'all', per_page: 100 });
    const mine = all.filter((pr) => pr.head?.ref?.includes(matchToken) && labelNames(pr).some((n) => n.startsWith('release:wave:')));
    const byWaveNum = {};
    for (const pr of mine) {
      const l = labelNames(pr).find((n) => n.startsWith('release:wave:'));
      if (l) byWaveNum[parseInt(l.split(':')[2], 10)] = pr;
    }
    const have4 = mine.length >= 4 && [0, 1, 2, 3].every((w) => byWaveNum[w] !== undefined);
    if (have4) {
      // 4 wave PRs exist — now confirm HIU has patched a manifest whose waves match them.
      const anchor = (await withRetry(() => github.rest.pulls.get({ owner, repo, pull_number: byWaveNum[0].number }), `fetch anchor #${byWaveNum[0].number}`)).data;
      const manifest = extractManifest(anchor.body);
      const matches = manifest && [0, 1, 2, 3].every((w) => manifest.waves[String(w)] === byWaveNum[w].number);
      if (matches) {
        core.info(`waitForWavePrs(${matchToken}): 4 wave PRs + matching manifest after ${i} poll(s)`);
        return findWavePrs(github, owner, repo, { matchToken, deployLabel }, core, all);
      }
      core.info(`waitForWavePrs(${matchToken}): 4 wave PRs present but manifest not yet patched (poll ${i}/${tries})`);
    } else {
      core.info(`waitForWavePrs(${matchToken}): ${mine.length} wave PR(s) so far (poll ${i}/${tries})`);
    }
    await new Promise((r) => setTimeout(r, delayMs));
  }
  // Timed out → let findWavePrs produce the authoritative failure (setFailed + throw).
  return findWavePrs(github, owner, repo, { matchToken, deployLabel }, core);
}
```

- [ ] **Step 3:** Syntax-check — `node -c .github/scripts/test-suite/promoter-drive-lib.js`.
- [ ] **Step 4: Commit** — `git add .github/scripts/test-suite/promoter-drive-lib.js && git commit -m "test(prb): validate wave-0 body manifest in findWavePrs; drop release:id"`

---

### Task B2: completion detection on the wave-0 anchor

The promoter writes `promoter:complete`/`:aborted` to the **wave-0 anchor PR** (release-promoter DESIGN §3: "release-level labels … live on the canonical anchor PR — the wave-0 PR"). The harnesses currently check the **last** wave (`byWave[3]`). Change each to `byWave[0]`.

**Files:** `promoter-drive.js`, `promoter-drive-parallel.js`, `promoter-drive-serialize.js`, `promoter-drive-failure.js`.

- [ ] **Step 1 — `promoter-drive.js` (~`:148`):** the "Done when the last wave's PR carries promoter:complete" check must read the anchor. Change the completion read from `prByWave[3]`/`byWave[3]` to `byWave[0]` (the anchor), e.g.:

```js
// Done when the wave-0 anchor PR carries promoter:complete (release-level marker).
const anchorPr = await fetchPr(byWave[0].number);
if (labelNames(anchorPr).includes(COMPLETE)) {
  core.info(`Release complete after ${tick} ticks (anchor #${byWave[0].number} carries ${COMPLETE}). Merged: ${mergedOrder}`);
  // ...existing post-completion assertions (merge order 0..3, monotonic timestamps) stay...
  return;
}
```

- [ ] **Step 2 — `promoter-drive-parallel.js` (`:136`):** change
```js
const completeFlags = states.map((st) => labelNames(prByNum[st.byWave[3].number]).includes(COMPLETE));
```
to
```js
const completeFlags = states.map((st) => labelNames(prByNum[st.byWave[0].number]).includes(COMPLETE));
```
Ensure `st.byWave[0].number` is in the per-tick `prByNum` prefetch (it already prefetches all waves incl. 0 at `:101`).

- [ ] **Step 3 — `promoter-drive-serialize.js`:** wherever release A's completion is detected via `prA[3]`/`byWave[3]`, change to `prA[0]`/`byWave[0]` (the anchor). (Locate the `labelNames(...).includes(COMPLETE)` check for A and repoint it to wave 0.)

- [ ] **Step 4 — `promoter-drive-failure.js` (`:135-136`):** the "must NOT be complete" check reads the last wave; the release-level marker lives on the anchor, so:
```js
const anchor = await fetchPr(byWave[0].number);
assert(!labelNames(anchor).includes(COMPLETE), core, `release must NOT be ${COMPLETE} on the wave-0 anchor — promotion halted at wave ${failWave}`);
```
(Keep the `promoter:failed` assertion on `byWave[failWave]` — the failure latch is on the failing wave's own PR, unchanged.)

- [ ] **Step 5:** `node -c` each edited file.
- [ ] **Step 6: Commit** — `git add .github/scripts/test-suite/promoter-drive*.js && git commit -m "test(prb): check promoter:complete/aborted on the wave-0 anchor (not last wave)"`

---

### Task B3: fix the stale cleanup comment (doc-only — F7)

**Files:** `.github/scripts/test-suite/cleanup.js` (the comment at `:7`).

Verified against the code: cleanup already iterates `WAVE_LABELS = ['release:wave:0','release:wave:1','release:wave:2','release:wave:3']` via `issues.listForRepo` (`:70-74`) and strips both `release:wave:*` and legacy `release:id:*` labels (`:86`). There is **no** single `release:wave:3` search seed to repoint — only a **stale comment** at `:7` describing the old discovery. So this is a doc-only fix; the all-wave scan + legacy `release:id:*` scrub already do the right thing.

- [ ] **Step 1:** Update the `:7` comment to describe anchor-based discovery (the promoter now anchors on `release:wave:0`); confirm the `release:id:*` scrub at `:86` stays (back-compat for PRs left by old runs that still carry the retired label).
- [ ] **Step 1b (F8 — harness comments/docs):** `grep -rn "release:id" .github/ README.md docs/` and update the remaining stale references that describe `release:id` as the contract — e.g. workflow comments in `.github/workflows/test-suite.yaml` and the header comment in `.github/scripts/test-suite/promoter-drive-serialize.js` (~`:10`): the per-app distinction is now by `instanceId` in the manifest, not `release:id`.
- [ ] **Step 2:** `node -c .github/scripts/test-suite/cleanup.js`.
- [ ] **Step 3: Commit** — `git add .github/scripts/test-suite/cleanup.js && git commit -m "test(prb): fix stale cleanup comment (anchor = release:wave:0)"`

---

### Task B4: wave-scenario fixtures are `tag.yaml`-only (Decision 1)

The promoter rejects a wave PR that changes any non-`tag.yaml` file. HIU's production override-removal would add `values.yaml` if a stack has `argocdApplication.appManifestsRevision != "main"`. Ensure the stacks used by the wave-drive scenarios carry **no such override**.

**Files:** stack dirs' `dummy-service/values.yaml` and `dummy-service-b/values.yaml` (parallel/serialize scenarios) across the wave-partitioned stacks.

- [ ] **Step 1:** Audit. First confirm the **static** fixtures are clean — `grep -rn "appManifestsRevision" --include=values.yaml .` at the repo root (verified currently empty). Then confirm the **wave-drive matrix cells do NOT trigger dynamic override setup**: the dedicated `override-removal*` scenarios create overrides on the fly in `test-suite.yaml` (~`:259-328`) and `update-image-tag.yaml` overlays `stack-metadata.yaml` before HIU runs — neither runs for the `promoter_drive` cells. So the audit target is the **overlaid working tree** for the wave scenarios, not just a static grep; confirm no `promoter_drive` cell sets an override.
- [ ] **Step 2:** If any wave-scenario `values.yaml` sets a non-`main` `appManifestsRevision`, remove that field (so override-removal is a no-op and wave PRs stay `tag.yaml`-only). Do NOT touch fixtures owned by the dedicated `override-removal*` scenarios.
- [ ] **Step 3: Commit (only if changed)** — `git add -A && git commit -m "test(prb): wave-scenario fixtures carry no branch override (tag.yaml-only)"`

---

# Part C — live end-to-end test

**Pre-reqs (user-owned infra, verify before dispatch):**
- Repo variable `PROMOTER_SMOKE_ENABLED == 'true'` on `keboola/helm-image-updater-testing`.
- Promoter dev GitHub App (id `895528`) installed on the repo with bypass + PR write/merge.
- Secrets present: `PROMOTER_DEV_GITHUB_APP_KEY`, `PROMOTER_SLACK_TOKEN`.
- Image `us-central1-docker.pkg.dev/keboola-prod-artifacts/release-promoter/release-promoter:dev-ST-4034-1` exists (already pushed). *(Its promoter code == the shipped manifest implementation; rebuild a fresh `dev-*` tag only if the promoter source changed since.)*

### Task C1: push both branches

- [ ] **Step 1:** Push the HIU branch — `git -C ../helm-image-updater push -u origin <HIU-branch>`.
- [ ] **Step 2:** Push the HIU-testing branch — `git -C ../helm-image-updater-testing push -u origin <HIU-testing-branch>`.

### Task C2: dispatch the wave-drive suite

- [ ] **Step 1:** Dispatch focused on the promoter scenarios (faster) — from `helm-image-updater-testing`:
```bash
gh workflow run test-suite.yaml --ref <HIU-testing-branch> \
  -f helm-image-updater-branch=<HIU-branch> \
  -f promoter-image-tag=dev-ST-4034-1 \
  -f promoter-scenarios-only=true
```
- [ ] **Step 2:** Watch — `gh run list --workflow=test-suite.yaml --limit 1` then `gh run watch <id> --exit-status`.
- [ ] **Step 3:** Confirm the `gradual-rollout` scenario: HIU opens 4 wave PRs; the wave-0 anchor body carries a valid manifest; the promoter merges waves 0→1→2→3 in order; the **wave-0 anchor** ends with `promoter:complete`; Slack narrative posts to `#ops-dev-keboola-argo`.

### Task C3: iterate on failure

- [ ] **Step 1:** On a red scenario, pull the failing step log + the promoter `stdout`/`stderr` (the harness echoes them). Triage to HIU (manifest shape/missing), harness (assertion), or promoter (image) — verify the claim against code before changing anything (`superpowers:receiving-code-review`). Fix in the owning repo, re-commit, re-dispatch.

---

## Deferred follow-up (track separately — NOT in this plan)

**Production `tag.yaml`-only fix for override-removal.** HIU's `_check_and_remove_override` runs for `UpdateStrategy.PRODUCTION` (which includes wave strategies) and `_create_pr_plan` (`plan_builder.py:643-647`) appends the resulting `values.yaml` change to the wave PR's `files_to_commit`. A real overridden stack in a wave release would therefore put a non-`tag.yaml` file in a wave PR → the promoter's `wellFormed` check (7) fails → the release is held `Conflicted` and never merges. Options when picked up: (a) skip override-removal for `is_wave` deploys; (b) emit it as a separate auto-merged non-wave PR. This plan only ensures the **test fixtures** avoid the trigger.

---

## Self-review

- **Spec coverage:** manifest emission (A1,A5), removal of `release:id` (A4,A6), idempotency by instanceId (A4), harness manifest validation (B1), completion-on-anchor (B2), cleanup seed (B3), `tag.yaml`-only fixtures (B4), live e2e (C). The `tag.yaml`-only production fix is explicitly deferred per Decision 1.
- **Type/name consistency:** `manifest_context` keys (`app`, `instance_id`, `display_name`, `source_sha`, `source_pr`) are written in `_build_manifest_context` (A4) and read in `_patch_anchor_manifest` (A5). `build_manifest` kwargs (`app`, `instance_id`, `display_name`, `waves`, `source_sha`, `source_pr`) match both the helper (A1) and its caller (A5). `find_open_release_anchors`/`update_pull_request_body` defined in A2 and used in A4/A5. `extractManifest` defined+exported in B1 and used by `findWavePrs`.
- **Contract fidelity:** the emitted manifest fence (` ```json\n…\n``` `) and field validation match release-promoter `manifest.ts`/`shape.ts` exactly (anchorWave `0`, distinct positive int `waves`, machine-safe `instanceId`). The harness `extractManifest` mirrors the promoter's.
- **Placeholder scan:** none — every code step is concrete.
