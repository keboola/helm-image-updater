# Deploy strategies & release-promoter integration

**Status:** design (spec) · **Issue:** [ST-4034](https://linear.app/keboola/issue/ST-4034) · **Date:** 2026-06-05

## 1. Goal

Teach `helm-image-updater` (HIU) to emit the PRs that `release-promoter` consumes, so promoter
can stage a rollout across waves — **without breaking any current deploy**. Today HIU creates and
auto-merges PRs itself; promoter never sees a contract it understands. After this change, a deploy
launched with a *promoter strategy* produces a set of **wave PRs, created up front and left
unmerged**, carrying the labels promoter reads; promoter then merges them wave-by-wave as each soaks
and verifies.

The promoter label contract is the source of truth — see
`release-promoter/DESIGN.md` §2 (labels), §2 (strategy parameters), §2 (waves→stacks). This spec
only describes the **HIU** side (plus the `rollout_wave` metadata and the integration test).

## 2. The promoter contract HIU must satisfy

A **release** = the set of wave PRs in the stacks repo sharing one `release:id:<releaseId>` label,
all created up front by HIU (one chart, one new image tag, fanned out per wave). One release rolls
out **one app**. Per the contract:

- **Waves are fixed `0..3`.** Wave 3 is the canonical `PRlast` promoter anchors discovery on. Wave 0
  is the dev wave (no UAT, soak 0).
- Each wave PR edits **only** the `tag.yaml` files for that wave's stacks (`{stack}/{app}/tag.yaml`).
  Promoter derives a wave's membership from the PR diff; the `release:wave:N` label gives ordering.
- Labels HIU sets on **every** wave PR:
  - `release:id:<releaseId>` — opaque grouping key (see below).
  - `release:wave:N` — `N ∈ {0,1,2,3}`.
  - `deploy:<strategy>` — `gradual | critical | critical-manual-gate`.

  **`releaseId` construction & the label-length limit.** GitHub caps label names at **50 characters**,
  and the `release:id:` prefix already eats 11. A naive `<chart>-<image_tag>` (e.g.
  `connection-production-<40-hex-sha>` ≈ 58 chars) **overflows**. Since promoter treats `release:id`
  as **opaque** — it gets the app for per-app FIFO from the PR diff, not by parsing the label
  (DESIGN §2) — `releaseId` only has to be (a) **identical across all wave PRs of one release** and
  (b) **unique per release**. So HIU builds a **compact** id that fits: `<chart>-<short>` where
  `<short>` is a stable, collision-resistant slug of the image tag (e.g. a truncated content hash, or
  the tag's trailing short-sha), with the whole label kept under the limit (unit-tested). The exact
  slug scheme is an implementation detail; the invariants (fits, stable, unique, same across waves) are
  the contract.
- **Label provisioning (create-if-missing).** Applying a label name that does not yet exist in the repo
  is not guaranteed to auto-create it via the GitHub API. HIU must **ensure each label exists before
  applying it**: the fixed `release:wave:0..3` and `deploy:*` labels can be pre-created (idempotent
  "create if absent, tolerate already-exists"); the **dynamic `release:id:<releaseId>`** label must be
  created on demand the same way. (Confirm the exact GitHub `add-labels` behavior during
  implementation; provision defensively regardless.)
- **Idempotency (re-running HIU must not corrupt a release).** A retried workflow must **not** create a
  *second* set of four wave PRs with the same `release:id` — duplicate `release:wave:N` makes the
  release `¬wellFormed` and promoter holds it **`Conflicted`** (verified in
  `release-promoter/src/core/interpret/shape.ts`). Before creating wave PRs, HIU **searches existing
  (open) PRs by `release:id:<releaseId>`**; if a release with that id already exists it **no-ops/reuses**
  (or fails loudly with a recoverable message) — never silently fans out duplicates.
- **HIU never merges a wave PR — including wave 0.** Promoter owns every merge (gated by per-app
  FIFO). HIU still **auto-approves** the PRs (confirmed: `io_layer.py:344 _auto_approve_pr`, called for
  unmerged PRs today). **Note:** auto-approve ≠ authorization to merge under branch-protection rulesets
  — promoter's merge identity must itself be permitted to merge the auto-approved wave PR (see §7).
- HIU sets **none** of the `promoter:*` labels (those are promoter's, written from observed reality).

The three strategies are **identical from HIU's point of view** — same wave PRs, same labels; only the
`deploy:*` value differs. All soak timing, UAT gating, and the manual gate live entirely in promoter.

## 3. Public interface: `DEPLOY_STRATEGY`

One new knob. Action input `deploy-strategy` → env `DEPLOY_STRATEGY`:

```
DEPLOY_STRATEGY = standard (default) | cloud_multi_stage | gradual | critical | critical-manual-gate
```

`AUTOMERGE` stays a secondary modifier whose meaning depends on the mode:

| `DEPLOY_STRATEGY` | grouping | `AUTOMERGE=true` | `AUTOMERGE=false` / unset | promoter labels |
|---|---|---|---|---|
| **standard** (default) | single PR — except prod + `AUTOMERGE=false` → **per-stack** | merge the 1 PR | **per-stack** unmerged PRs, auto-approved (dev / single-stack / override → 1 PR) | none |
| **cloud_multi_stage** | cloud×stage PRs (≤6) | dev PRs merge, prod PRs never | all unmerged | none |
| **gradual / critical / critical-manual-gate** | per-wave PRs (0–3) | *(ignored)* — promoter merges all | promoter merges all | `release:id` · `release:wave:N` · `deploy:<strategy>` |

There are **no nonsensical combinations**: `AUTOMERGE` is simply ignored where the mode owns merging
(wave strategies; multi-stage prod).

**Decisions baked in (from brainstorming):**
- Wave mode **ignores `AUTOMERGE`** — promoter merges all waves incl. wave 0. (Auto-merging wave 0 in
  HIU would bypass promoter's per-app FIFO gate, so two concurrent same-app releases could hit dev out
  of order. Deferred as a possible explicit opt-in later.)
- `standard` keeps today's HIU grouping (no change): `AUTOMERGE=true` / dev / single-stack / override → one PR; prod + `AUTOMERGE=false` → one PR **per stack**.
- `MULTI_STAGE=true` becomes a **deprecated alias** for `DEPLOY_STRATEGY=cloud_multi_stage`
  (behavior identical). If both are set and disagree, `DEPLOY_STRATEGY` wins + a warning is logged.

**Validation:**
- An **unknown** `DEPLOY_STRATEGY` is a **hard config error** — never a silent fall-back to `standard`
  (a typo like `gradul` with `AUTOMERGE=true` must not quietly merge everywhere).
- `DEPLOY_STRATEGY ∈ {gradual, critical, critical-manual-gate}` requires a production/semver tag
  (`UpdateStrategy.PRODUCTION`). With a dev/canary tag or `OVERRIDE_STACK`, it is a config error
  (`validate()` returns a message). Wave mode also requires `IMAGE_TAG` (not just an `EXTRA_TAG`).
- `cloud_multi_stage` is production-only by the **same mechanism as today's `MULTI_STAGE`**: the
  cloud×stage grouping only fires for a production strategy (the existing `strategy==PRODUCTION` guard
  in grouping). There is **no new validation error** for `cloud_multi_stage` on a non-prod tag — that
  would be stricter than today, which silently no-ops multi-stage for non-prod.
- **Action input default is empty** (`deploy-strategy: ''`), not `standard`: the GitHub Action always
  passes the input through, and a non-empty `DEPLOY_STRATEGY` is treated as *explicit* (overriding
  `MULTI_STAGE`). An empty default lets legacy `multi-stage: true` callers keep aliasing to
  `cloud_multi_stage`; `from_env` maps empty → unset → `standard`.

## 4. Wave→stack mapping: `rollout_wave`

Per-stack, in each stack's `stack-metadata.yaml`. A flat key:

```yaml
# <stack>/stack-metadata.yaml
rollout_wave: 1        # integer 0..3
```

**Where it lives today:** kbc-stacks already has `stack-metadata.yaml` in (almost) every stack dir, so
production stacks gain the `rollout_wave` key in their existing file (user-authored). The
**helm-image-updater-testing repo has *no* `stack-metadata.yaml` files at all** (verified: 0 present,
only `shared-values.yaml`) — so **PR-B creates them** for the mock stacks. HIU's reader must therefore
**tolerate an absent file/field** and fall back to the default below.

**The single wave-assignment rule** (resolves the dev-vs-default ambiguity):

> `wave(stack)` = explicit `rollout_wave` from `stack-metadata.yaml` if present; otherwise a
> **classification-aware default**: dev stacks (`classify_stack().is_dev`) → **0**, all other stacks →
> **3**. An explicit `rollout_wave` always wins.

So a dev stack with no metadata still lands in wave 0, and an unclassified production stack rolls out
last (wave 3) — no per-dev-stack metadata is *required*, but an explicit value is honored.

- **Wave-mode target universe** = all discovered stacks that have the app's `{stack}/{app}/tag.yaml`,
  **excluding** canary, excluded, and `*-e2e` stacks (today's production exclusions) — but **including**
  the dev stacks (they become wave 0). Each target stack is assigned `wave(stack)` per the rule above.

**Edge cases:**
- Promoter requires waves **contiguous from 0** (verified: `interpret/shape.ts` rejects gaps), and wave
  3 must exist (`PRlast`). So HIU **requires all of waves 0..3 to be non-empty** for a valid wave deploy
  and **fails loud** otherwise (rather than emitting a gapped `{0,1,3}` that promoter would hold
  `Conflicted`).
- A target stack has no `{stack}/{app}/tag.yaml` (app not deployed there) — skip that stack.

## 5. Internal architecture (refactor, inspired by PR #19)

PR #19 (`kacurez-grouping-strategy`) is **superseded** — we salvage only its core idea (separate
*how many PRs* from *do we merge*) and rebuild it around `DEPLOY_STRATEGY`. Close #19. Changes:

- **`models.py`**: add `class DeployStrategy(Enum)` = `STANDARD | CLOUD_MULTI_STAGE | GRADUAL |
  CRITICAL | CRITICAL_MANUAL_GATE`. Add `PRPlan.labels: List[str] = field(default_factory=list)`.
  Optionally add `wave_number`/`release_id` to the PR-group dicts.
- **`environment.py`**: parse `DEPLOY_STRATEGY` (default `standard`; **unknown → hard validation
  error**, never a silent `standard` fall-back); fold the `MULTI_STAGE` alias; add the
  wave-mode-requires-prod-tag validation.
- **`plan_builder.py`**: replace the strategy-tangled `_group_changes_for_prs()` (lines 401–490) with a
  small dispatch keyed on `config.deploy_strategy`:
  - `STANDARD` → today's HIU grouping, unchanged (`pr_type='standard'`): one PR for `automerge=true` /
    dev / single-stack / override; one PR **per stack** for prod + `automerge=false`.
  - `CLOUD_MULTI_STAGE` → today's cloud×stage grouping (moved verbatim), `pr_type='multi_stage_{dev|prod}'`.
  - `GRADUAL|CRITICAL|CRITICAL_MANUAL_GATE` → one group per wave `w` with ≥1 change, `pr_type='wave'`,
    plus `wave_number=w`, `release_id`, and `labels=[release:id:…, release:wave:w, deploy:…]`.
  - Canary tag → canary grouping as today (independent of `DEPLOY_STRATEGY`).
- **`_should_auto_merge(deploy_strategy, pr_type, user_requested)`** (lines 600–616): canary → `True`;
  `pr_type='wave'` → `False`; `pr_type='multi_stage_prod'` → `False`; else → `user_requested`.
- **`_create_pr_plan()`** (lines 493–586): wave branch/title carry the wave + release id (e.g.
  `…-wave1-…`, title `[gradual wave 1] <chart>@<tag> …`); set `PRPlan.labels` from the group.
- **Label plumbing — the full path, not just `create_pull_request`.** Labels must be threaded through
  every layer: `PRPlan.labels` → `plan_executor._execute_pr_plans()` (≈`plan_executor.py:69`) →
  `io_layer.create_branch_commit_and_pr()` (`io_layer.py:378`) → `create_pull_request()`
  (`io_layer.py:240`). Both intermediate signatures currently take **no** `labels` arg — add it. In
  `create_pull_request()`: **provision** the labels (create-if-missing, §2) then **apply** them
  (`pr.add_to_labels`/`set_labels`) regardless of auto-merge; keep the existing auto-approve path. The
  **dry-run** output prints the labels that *would* be applied (per PR). (No `ExecutionResult.labels`
  field — nothing consumes it; the dry-run print is the observable output.)

## 6. Behavior changes vs today (explicit)

| Path | Today | After |
|---|---|---|
| `standard` + `AUTOMERGE=true` (prod) | single PR, merged | **unchanged** |
| `standard` + `AUTOMERGE=false` (prod) | **per-stack** PRs | **unchanged** |
| dev tag | single PR, merged | **unchanged** |
| canary | single PR, merged | **unchanged** |
| `MULTI_STAGE=true` | cloud×stage | **unchanged** (now via `cloud_multi_stage` alias) |
| wave strategies | — | **new**: wave PRs + labels, promoter-merged |

⚠️ The only change to an existing deploy path. It is desired (kills the 20-PR explosion) but it
**changes production `automerge=false` behavior**. Concrete blast radius (verified):
- **HIU-testing** `dont-merge` scenario currently asserts `expected_count: 9` (per-stack)
  (`test-suite.yaml:149`) → must become **1 PR** (+ the same 9 modified `tag.yaml` files). Updated in PR-B.
- **HIU unit tests** don't currently assert prod `automerge=false` grouping at all
  (`test_plan_builder.py:245` only covers `automerge=True`), so a new test is *added*, none rewritten.

## 7. Non-goals (out of scope)

- No `release-promoter` code changes — only a DESIGN.md note dropping the "HIU is not modified" MVP
  caveat (§2).
- No HIU-side soak/UAT/merge logic — promoter owns all of that.
- No `SINGLE`/`STACK` grouping strategies from PR #19 (not needed).
- HIU does not write `rollout_wave` data for production; the mapping is authored in kbc-stacks (the
  test mocks' `stack-metadata.yaml` files **are** created here, in PR-B).
- Auto-merge-wave-0 fast path: deferred.

**External prerequisite (not built here, but must hold):** promoter's merge identity must be authorized
to merge an auto-approved wave PR under branch-protection/CODEOWNERS rulesets — in production (the
promoter GitHub App) and in HIU-testing (the workflow `GITHUB_TOKEN`). The integration test surfaces
this as an explicit precondition (§8b.5).

## 8. Testing

### 8a. HIU unit tests (strict TDD — failing test first)

- `DEPLOY_STRATEGY` parsing + `MULTI_STAGE` alias; **unknown value → validation error** (not `standard`);
  wave-mode-requires-prod-tag validation.
- Grouping: wave splits target stacks by `wave(stack)` (explicit `rollout_wave`, else dev→0 / other→3);
  all-waves-0..3-non-empty rule (gapped → error); `standard` always one PR **including
  `automerge=false`** (new test — was uncovered); `cloud_multi_stage` parity with old multi-stage;
  canary unchanged.
- `_should_auto_merge`: `wave→False`, `multi_stage_prod→False`, `canary→True`, else passthrough.
- Labels (**invariants, not a literal string** — `releaseId` is compacted, §2): each wave PR carries a
  `release:id:*`, a `release:wave:N`, and a `deploy:<strategy>` label; the `release:id` label is
  **≤ GitHub's 50-char limit**, **identical across all 4 wave PRs** of a run, and **distinct** for
  different `<chart>/<tag>` inputs; non-wave PRs get no promoter labels.
- **Idempotency**: re-running the same wave deploy does not produce a second set of wave PRs (search by
  `release:id` → reuse/no-op or loud recoverable error).
- `rollout_wave` reader: default rule (dev→0, other→3), explicit value wins, missing-file tolerated,
  integer coercion/validation.
- Label plumbing reaches the executor + `create_branch_commit_and_pr` + dry-run output (not only
  `_create_pr_plan`).

### 8b. HIU-testing integration test (PR-B): **full gradual soak progression**

**Mocks (created in PR-B):** add a `stack-metadata.yaml` with `rollout_wave: 0..3` to the 11 testing
stacks so `dummy-service` spans all four waves (≥1 `dummy-service` stack per wave; dev stacks → wave 0;
all of 0..3 non-empty).

**Why a multi-tick state machine (not one tick per wave).** Verified against the promoter core: a
single tick sets `promoter:synced` and triggers UAT, but the **latch *timestamp* is read from the
label on a *subsequent* tick** (`interpret/shape.ts` reads `labelTimestamps[SYNCED]`; `verify.ts`
sets the label this tick), and promotion's soak check compares `now − latchAt` (`promote.ts:106-109`).
So synced→uat→verified→promote spans **several ticks**, and the **fake `world.clock` must be
re-anchored to ≥ the real label time** before soak can elapse (even soak-0: `now < latchAt` ⇒ blocked).

Scenario, added to `test-suite.yaml` (gated like the existing promoter smoke step):

1. **Trigger HIU** `deploy-strategy=gradual` on `dummy-service` with a `production-<random>` tag →
   assert exactly **4 wave PRs** (waves 0–3), each unmerged, with the correct `release:id` /
   `release:wave:N` / `deploy:gradual` labels and correct per-wave stack membership.
2. **Drive promoter** (Docker image, `--source live-only-github` — real GitHub + faked
   argo/clock/slack via `world.json`, **real** commit-ancestry) in a harness loop (node script,
   matching the existing `test-suite/*.js` pattern). The loop is a **state machine** with these moves
   between ticks (each "tick" = one `reconcile` run; `saveWorld()` persists clock/world mutations):
   - **Init**: `world.json` `clock = <real now>`, empty `appStatuses`/`uatRuns`. Tick → merges **wave 0**
     (FIFO-active, no predecessor gate).
   - **After any merge** of wave `w`: `git fetch`; set each `<app>-<stack>` of wave `w` to
     `{ status:'Synced', health:'Healthy', syncedRevision: <origin/main HEAD> }` (a real commit ≥ the
     merge commit, so real ancestry resolves *ahead*). Tick → promoter latches `promoter:synced@w`
     (and, for `w>0`, emits `RunUAT`).
   - **For `w>0`**: set `uat-<stack>` runs to `{ phase:'Succeeded', revision: <≥ merge commit> }`
     — **note the UAT shape has no `health` field** (`UatRun = {stack, revision, phase}`). Tick →
     promoter latches `promoter:uat-passed@w`. (Wave `w` is now *Verified* as of this label.)
   - **Soak/promote**: re-anchor the clock to capture the real latch time, then jump past the soak —
     `promoter dev set-clock now` **then** `promoter dev advance-clock <soakAfter[gradual][w] + ε>`
     (gradual: 1h after w1, 1h after w2; w0 & w3 soak 0). Tick → promoter merges wave `w+1`.
   - Repeat for waves 1→2→3. After wave 3 is Verified (soak 0), the next tick sets
     **`promoter:complete`** on the wave-3 PR.
3. **Assertions**: each wave merges only after its predecessor is Done; waves merge **in order**; the
   release reaches `promoter:complete`; **bounded** tick budget (fail if it stalls — guards against the
   clock-not-re-anchored trap above).
4. **Regression**: the must-not-merge guard (PR #1487) still holds for the `standard` scenarios; update
   the `dont-merge` scenario's `expected_count` 9 → 1 for the new single-PR behavior.
5. **Merge-authorization precondition**: confirm the promoter merge identity used in HIU-testing (the
   workflow `GITHUB_TOKEN`) can actually **merge** an auto-approved wave PR under the testing repo's
   branch protection. If a ruleset blocks it, the test can't progress — document/relax as needed (§7).
6. **Cleanup**: close any still-open wave PRs + delete their branches; merged PRs are done (main
   advances by the wave merges — acceptable for a fixture repo).

## 9. Deliverables / PR plan

- **PR-A — helm-image-updater** (new branch, supersedes & closes #19): §3 interface, §5 refactor, §6
  behavior change, §8a unit tests. Aim for one PR; split off a pure refactor commit/PR only if it
  balloons.
- **rollout_wave metadata**: authored in kbc-stacks `stack-metadata.yaml` (user-owned) and in the
  HIU-testing mocks (part of PR-B).
- **PR-B — helm-image-updater-testing**: §8b gradual soak-progression test + updated `standard`
  assertions.
- **release-promoter DESIGN.md**: drop the "helm-image-updater is *not* modified for MVP" note (§2).

## 10. Open questions

- Final home for this spec / Linear linking (currently HIU repo root, matching existing design docs).
- Is the kbc-stacks `rollout_wave` rollout a separate user-owned PR, or in scope here? (Assumed
  user-owned for production; test mocks are in PR-B.)
- Sparse waves: we **require all of 0..3 non-empty** and error otherwise (§4). Auto-renumbering a
  sparse set into a contiguous `0..k` is deferred — it would require promoter to drop the fixed-0..3
  assumption (DESIGN notes `release:wave:0` anchoring would be count-agnostic).
- `releaseId` slug scheme — exact form (truncated SHA-256 of `<chart>\0<tag>` vs trailing tag short-sha)
  to be picked during implementation; either satisfies the §2 invariants.
- Confirm GitHub's `add-labels` behavior (does it auto-create absent labels?) — provisioning is
  defensive either way (§2), but the answer decides whether the pre-create step is strictly required.
