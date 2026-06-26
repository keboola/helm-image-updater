"""ST-4157: promoter-managed `manual-per-stack` deploy = one PR per prod stack, NO waves.

A human merges each member PR in any order; release-promoter completes the release once
all are merged + synced. Activation: explicit DEPLOY_STRATEGY=manual-per-stack on a
production/semver tag (AUTOMERGE ignored, like the wave strategies). The anchor = the
lowest-numbered member PR; it carries `release:anchor` + a `mode:"manual-per-stack"`
manifest whose `members` are the member PR numbers. Members carry `deploy:manual-per-stack`
but no `release:wave:*` / `release:anchor`.
"""

import os
import re
from unittest.mock import Mock

import pytest

from helm_image_updater.models import UpdateStrategy, DeployStrategy
from helm_image_updater.environment import EnvironmentConfig
from helm_image_updater.io_layer import IOLayer
from helm_image_updater.manifest import build_manual_manifest, is_manifest_v1, extract_instance_id, manifest_block
from helm_image_updater.plan_builder import (
    prepare_plan,
    _group_changes_for_prs,
    _group_changes_manual_per_stack,
    _is_promoter_managed_manual_per_stack,
    _should_auto_merge,
)
from helm_image_updater.plan_executor import execute_plan
from helm_image_updater.message_generation import manual_release_search_link


PROD_STACKS = [
    "com-keboola-azure-north-europe",
    "kbc-us-east-1",
    "cloud-keboola-cs",
]
DEV_STACKS = [
    "dev-keboola-gcp-us-central1",
    "dev-keboola-aws-eu-west-1",
]


def _stack_change(stack):
    return {"stack": stack, "file_change": Mock(), "changes": []}


def _manual_config():
    config = Mock()
    config.deploy_strategy = DeployStrategy.MANUAL_PER_STACK
    config.automerge = False
    # Mock auto-creates truthy attrs; pin the OTHER promoter gates off so routing
    # exercises the manual branch (a real manual EnvironmentConfig has these False).
    config.promoter_managed_standard = False
    return config


def _manual_plan():
    plan = Mock()
    plan.strategy = UpdateStrategy.PRODUCTION
    plan.multi_stage = False
    plan.helm_chart = "dummy-service"
    plan.image_tag = "production-abc123"
    return plan


# --- models / enum -----------------------------------------------------------------


def test_manual_per_stack_is_not_a_wave_strategy():
    assert DeployStrategy("manual-per-stack") is DeployStrategy.MANUAL_PER_STACK
    assert DeployStrategy.MANUAL_PER_STACK.is_wave is False


# --- grouping: one PR per stack (dev + prod), deploy label, no wave -----------------


def test_group_manual_one_pr_per_stack_shape():
    # Shape check on a prod-only input (dev inclusion is covered by the test below);
    # one group per stack with the manual label, no wave/anchor labels.
    changes = [_stack_change(s) for s in PROD_STACKS]
    groups = _group_changes_manual_per_stack(changes, _manual_plan(), _manual_config())

    assert len(groups) == len(PROD_STACKS)
    for g in groups:
        assert len(g["stacks"]) == 1
        assert g["pr_type"] == "manual"
        assert g["base_branch"] == "main"
        assert g["labels"] == ["deploy:manual-per-stack"]
        assert "wave_number" not in g
        assert not any(l.startswith("release:wave:") for l in g["labels"])
        assert not any(l == "release:anchor" for l in g["labels"])  # added at executor time
    assert sorted(s for g in groups for s in g["stacks"]) == sorted(PROD_STACKS)


def test_group_manual_includes_dev_and_prod_stacks():
    # A production tag deploys to BOTH dev and prod stacks (only PROD stacks are
    # tag-restricted), so manual-per-stack opens one PR per stack across both tiers.
    changes = [_stack_change(s) for s in PROD_STACKS + DEV_STACKS]
    groups = _group_changes_manual_per_stack(changes, _manual_plan(), _manual_config())
    all_stacks = sorted(s for g in groups for s in g["stacks"])
    assert all_stacks == sorted(PROD_STACKS + DEV_STACKS)
    assert len(groups) == len(PROD_STACKS) + len(DEV_STACKS)
    for g in groups:
        assert len(g["stacks"]) == 1
        assert g["labels"] == ["deploy:manual-per-stack"]


def test_group_manual_excludes_e2e_stacks():
    # e2e stacks are excluded via the canonical EXCLUDED_STACKS (classify_stack) — NOT a name
    # suffix. A real EXCLUDED_STACKS entry is dropped; a new e2e stack must be listed there
    # (Halama review — the brittle `endswith('-e2e')` heuristic was removed).
    from helm_image_updater.config import EXCLUDED_STACKS
    e2e = EXCLUDED_STACKS[0]
    changes = [_stack_change(s) for s in PROD_STACKS] + [_stack_change(e2e)]
    groups = _group_changes_manual_per_stack(changes, _manual_plan(), _manual_config())
    assert e2e not in [s for g in groups for s in g["stacks"]]
    assert sorted(s for g in groups for s in g["stacks"]) == sorted(PROD_STACKS)


def test_group_manual_drops_canary_and_unclassified_stacks():
    # Positive predicate (is_dev or is_production): a canary stack — neither dev nor prod —
    # is dropped, never mis-binned as a member (mirrors the standard 2-wave prod-wave guard).
    changes = [_stack_change(s) for s in PROD_STACKS]
    changes.append(_stack_change("dev-keboola-canary-orion"))  # canary: not dev, not prod
    groups = _group_changes_manual_per_stack(changes, _manual_plan(), _manual_config())
    assert "dev-keboola-canary-orion" not in [s for g in groups for s in g["stacks"]]


# --- routing + auto-merge ----------------------------------------------------------


def test_group_changes_for_prs_routes_manual_per_stack():
    changes = [_stack_change(s) for s in PROD_STACKS]
    groups = _group_changes_for_prs(changes, _manual_plan(), _manual_config(), Mock())
    assert len(groups) == len(PROD_STACKS)
    assert all(g["pr_type"] == "manual" for g in groups)


def test_should_auto_merge_manual_is_false():
    plan = _manual_plan()
    assert _should_auto_merge(plan, "manual", user_requested=True) is False
    assert _should_auto_merge(plan, "manual", user_requested=False) is False


def test_is_promoter_managed_manual_per_stack_production_only():
    assert _is_promoter_managed_manual_per_stack(_manual_config(), _manual_plan()) is True
    # OVERRIDE / CANARY / DEV are orthogonal axes — never the manual path.
    for axis in (UpdateStrategy.OVERRIDE, UpdateStrategy.CANARY, UpdateStrategy.DEV):
        plan = _manual_plan()
        plan.strategy = axis
        assert _is_promoter_managed_manual_per_stack(_manual_config(), plan) is False


# --- guard regressions: canary / override not hijacked -----------------------------


def test_canary_not_hijacked_by_manual_per_stack():
    config = _manual_config()
    config.image_tag = "canary-orion-abc123"
    plan = _manual_plan()
    plan.strategy = UpdateStrategy.CANARY
    plan.image_tag = "canary-orion-abc123"
    changes = [_stack_change(s) for s in PROD_STACKS]
    groups = _group_changes_for_prs(changes, plan, config, Mock())
    assert len(groups) == 1
    assert groups[0]["pr_type"] == "canary"


def test_override_not_hijacked_by_manual_per_stack():
    config = _manual_config()
    plan = _manual_plan()
    plan.strategy = UpdateStrategy.OVERRIDE
    changes = [_stack_change("kbc-us-east-1")]
    groups = _group_changes_for_prs(changes, plan, config, Mock())
    assert len(groups) == 1
    assert groups[0]["pr_type"] == "standard"
    assert groups[0].get("labels", []) == []


# --- manifest: manual variant ------------------------------------------------------


def test_build_manual_manifest_shape():
    m = build_manual_manifest(
        app="dummy-service", instance_id="dummy-service-abc123def456",
        display_name="dummy-service@production-abc123", members=[18, 12, 15],
    )
    assert m["manifestVersion"] == "v1"
    assert m["mode"] == "manual-per-stack"
    assert m["members"] == [12, 15, 18]  # sorted
    assert "waves" not in m and "anchorWave" not in m
    assert is_manifest_v1(m) is True
    # extractable for the idempotency guard
    assert extract_instance_id(manifest_block(m)) == "dummy-service-abc123def456"


def test_is_manifest_v1_rejects_manual_with_empty_or_bad_members():
    base = dict(manifestVersion="v1", mode="manual-per-stack", instanceId="x-1",
                displayName="x", app="x")
    assert is_manifest_v1({**base, "members": []}) is False
    assert is_manifest_v1({**base, "members": [1, 1]}) is False
    assert is_manifest_v1({**base, "members": [0]}) is False
    assert is_manifest_v1({**base, "members": [1, 2]}) is True


def test_is_manifest_v1_rejects_wave_manifest_with_present_mode_key(monkeypatch=None):
    # F2 mirror (Codex): the promoter rejects ANY present `mode` key that isn't
    # "manual-per-stack" (JSON null is present-and-non-undefined → reject), even on an
    # otherwise-valid wave manifest. HIU must reject the same.
    wave = {"manifestVersion": "v1", "instanceId": "x-1", "displayName": "x", "app": "x",
            "anchorWave": 0, "waves": {"0": 10}}
    assert is_manifest_v1(wave) is True                 # no mode key → valid wave manifest
    assert is_manifest_v1({**wave, "mode": None}) is False     # present null mode → reject
    assert is_manifest_v1({**wave, "mode": "wave"}) is False   # present unknown mode → reject


# --- prepare_plan + execute_plan (integration) -------------------------------------


def _make_tag_yaml(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("image:\n  tag: old-tag\n")


@pytest.fixture
def manual_stacks(tmp_path):
    """Three real PROD stacks on disk for a `test-chart` deploy."""
    for s in PROD_STACKS:
        _make_tag_yaml(tmp_path / s / "test-chart" / "tag.yaml")
    return tmp_path


def _manual_env(base_dir, dry_run="true", automerge="false"):
    return {
        "HELM_CHART": "test-chart",
        "IMAGE_TAG": "production-abc123",
        "GH_TOKEN": "t",
        "GH_APPROVE_TOKEN": "a",
        "DEPLOY_STRATEGY": "manual-per-stack",
        "AUTOMERGE": automerge,
        "DRY_RUN": dry_run,
        "TARGET_PATH": str(base_dir),
    }


def test_env_recognises_manual_per_stack():
    config = EnvironmentConfig.from_env(_manual_env("/tmp"))
    assert config.deploy_strategy == DeployStrategy.MANUAL_PER_STACK
    assert config.promoter_managed_manual_per_stack is True
    assert config.validate() == []


def test_prepare_plan_manual_one_pr_per_stack_unmerged_labelled(manual_stacks):
    os.chdir(manual_stacks)
    config = EnvironmentConfig.from_env(_manual_env(manual_stacks))
    plan = prepare_plan(config, IOLayer(Mock(), Mock(), dry_run=True, approve_github_repo=Mock()))

    assert plan.manifest_context is not None
    assert plan.manifest_context["instance_id"].startswith("test-chart-")
    assert len(plan.pr_plans) == len(PROD_STACKS)
    for p in plan.pr_plans:
        assert p.auto_merge is False
        assert p.labels == ["deploy:manual-per-stack"]
        assert p.wave_number is None
        assert p.manual_member is True


def test_manual_release_search_link_uses_app_and_strategy_labels_and_tag():
    from urllib.parse import unquote
    url = manual_release_search_link("keboola/kbc-stacks", "gooddata-cn-provisioning",
                                     "production-c7b8448a924d", None)
    assert url.startswith("https://github.com/keboola/kbc-stacks/pulls?q=")
    q = unquote(url)
    assert 'label:"app:gooddata-cn-provisioning"' in q
    assert 'label:"deploy:manual-per-stack"' in q
    assert "production-c7b8448a924d" in q


def test_manual_member_pr_bodies_link_to_all_release_prs(manual_stacks):
    # Every member PR (anchor incl.) must carry a "### Release" link that finds the whole
    # manual-per-stack release (the wave PRs have one; manual members were missing it).
    from helm_image_updater.config import GITHUB_REPO
    os.chdir(manual_stacks)
    config = EnvironmentConfig.from_env(_manual_env(manual_stacks))
    plan = prepare_plan(config, IOLayer(Mock(), Mock(), dry_run=True, approve_github_repo=Mock()))
    expected = manual_release_search_link(GITHUB_REPO, "test-chart", "production-abc123", [])
    assert len(plan.pr_plans) == len(PROD_STACKS)
    for p in plan.pr_plans:
        assert "### Release" in p.pr_body
        assert "manual-per-stack release" in p.pr_body
        assert expected in p.pr_body


def test_execute_plan_manual_anchors_lowest_pr_and_patches_member_manifest(manual_stacks):
    os.chdir(manual_stacks)
    config = EnvironmentConfig.from_env(_manual_env(manual_stacks, dry_run="false"))

    # io: 3 PR creates returning sequential numbers; capture body-patch + label-add.
    io = IOLayer(Mock(), Mock(), dry_run=False, approve_github_repo=Mock())
    io.find_open_release_anchors = Mock(return_value=[])
    pr_seq = iter([105, 101, 108])  # creation order ≠ numeric order; anchor must be min(101)

    create_calls = []

    def _create(**kw):
        n = next(pr_seq)
        create_calls.append({"labels": kw.get("labels"), "auto_merge": kw.get("auto_merge"), "num": n})
        return f"https://github.com/keboola/kbc-stacks/pull/{n}"

    io.create_branch_commit_and_pr = Mock(side_effect=_create)
    io.write_file_changes = Mock()
    io.update_pull_request_body = Mock()
    io.add_label = Mock()

    plan = prepare_plan(config, io)
    result = execute_plan(plan, io)

    # 3 member PRs, each unmerged + deploy:manual-per-stack, no release:wave/anchor at create.
    assert len(create_calls) == 3
    for c in create_calls:
        assert c["auto_merge"] is False
        assert c["labels"] == ["deploy:manual-per-stack"]
        assert not any(str(l).startswith("release:wave:") for l in c["labels"])
        assert "release:anchor" not in c["labels"]

    # release:anchor added to the LOWEST-numbered PR (101), exactly once.
    io.add_label.assert_called_once_with(101, "release:anchor")

    # the manifest is patched into the anchor (101) with mode + all member numbers.
    assert io.update_pull_request_body.call_count == 1
    anchor_arg, body_arg = io.update_pull_request_body.call_args[0]
    assert anchor_arg == 101
    iid = extract_instance_id(body_arg)
    assert iid is not None and iid.startswith("test-chart-")
    # parse the embedded manifest and check it's the manual variant with all 3 members
    m = re.search(r"```json\n(.*?)```", body_arg, re.DOTALL)
    assert m is not None
    import json
    manifest = json.loads(m.group(1))
    assert manifest["mode"] == "manual-per-stack"
    assert sorted(manifest["members"]) == [101, 105, 108]
    assert is_manifest_v1(manifest) is True
    assert result.success is True


def test_find_open_release_anchors_searches_wave0_and_release_anchor(monkeypatch=None):
    # H2: the idempotency guard must also see manual anchors (release:anchor), not just
    # wave-0 anchors — else a re-run double-opens a manual release.
    gh = Mock()
    gh.get_issues = Mock(return_value=[])
    io = IOLayer(Mock(), gh, dry_run=False, approve_github_repo=Mock())

    io.find_open_release_anchors()

    label_args = [c.kwargs.get("labels") for c in gh.get_issues.call_args_list]
    assert ["release:wave:0"] in label_args
    assert ["release:anchor"] in label_args


def test_prepare_plan_manual_invokes_idempotency_guard(manual_stacks):
    # H2: a non-dry-run with an already-open MANUAL anchor (same instanceId) must raise.
    import base64
    import json as _json
    from helm_image_updater.manifest import build_manual_manifest, manifest_block, compute_instance_id

    os.chdir(manual_stacks)
    env = _manual_env(manual_stacks, dry_run="false")
    env["METADATA"] = base64.b64encode(
        _json.dumps({"source": {"sha": "deadbeef0123abc"}}).encode()
    ).decode()
    config = EnvironmentConfig.from_env(env)

    iid = compute_instance_id("test-chart", "deadbeef0123abc", "production-abc123")
    anchor_body = manifest_block(build_manual_manifest(
        app="test-chart", instance_id=iid, display_name="test-chart@production-abc123",
        members=[9, 12, 15],
    ))
    io = IOLayer(Mock(), Mock(), dry_run=False, approve_github_repo=Mock())
    io.find_open_release_anchors = Mock(return_value=[(9, anchor_body)])

    with pytest.raises(RuntimeError, match="already has an open anchor"):
        prepare_plan(config, io)


def test_execute_plan_manual_anchor_label_failure_closes_members(manual_stacks):
    # Codex finding: release:anchor is applied BEFORE the body patch, so if the label-add
    # fails the members exist with no anchor + no manifest (undiscoverable, rerun-duplicable).
    # The executor must close the created members (mirror the F3 cleanup), not leave them.
    os.chdir(manual_stacks)
    config = EnvironmentConfig.from_env(_manual_env(manual_stacks, dry_run="false"))
    io = IOLayer(Mock(), Mock(), dry_run=False, approve_github_repo=Mock())
    io.find_open_release_anchors = Mock(return_value=[])
    pr_seq = iter([101, 105, 108])
    io.create_branch_commit_and_pr = Mock(
        side_effect=lambda **kw: f"https://github.com/keboola/kbc-stacks/pull/{next(pr_seq)}")
    io.write_file_changes = Mock()
    io.update_pull_request_body = Mock()
    io.close_pr = Mock()
    io.add_label = Mock(side_effect=Exception("label boom"))

    plan = prepare_plan(config, io)
    result = execute_plan(plan, io)

    assert result.success is False
    io.update_pull_request_body.assert_not_called()  # never reached the manifest patch
    closed = sorted(c.args[0] for c in io.close_pr.call_args_list)
    assert closed == [101, 105, 108]
