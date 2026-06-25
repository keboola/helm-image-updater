"""ST-4126: promoter-managed `standard` deploy = 2-wave dev→prod release.

Wave 0 = all dev stacks (anchor, carries the manifest), wave 1 = all prod stacks.
The cloud dimension is collapsed (no per-cloud split). Activation: explicit
DEPLOY_STRATEGY=standard + automerge=false on a production/semver tag.
"""

import os
from unittest.mock import Mock

import pytest

from helm_image_updater.models import UpdateStrategy, DeployStrategy
from helm_image_updater.environment import EnvironmentConfig
from helm_image_updater.io_layer import IOLayer
from helm_image_updater.plan_builder import (
    prepare_plan,
    _group_changes_for_prs,
    _group_changes_standard_2wave,
    _should_auto_merge,
)


# Real stack names whose dev/prod classification is driven by config.DEV_STACK_MAPPING.
DEV_STACKS = [
    "dev-keboola-gcp-us-central1",   # gcp dev
    "kbc-testing-azure-east-us-2",   # azure dev
    "dev-keboola-aws-eu-west-1",     # aws dev
]
PROD_STACKS = [
    "com-keboola-azure-north-europe",
    "kbc-us-east-1",
    "cloud-keboola-cs",
]


def _stack_change(stack):
    return {"stack": stack, "file_change": Mock(), "changes": []}


def _std_config():
    config = Mock()
    config.deploy_strategy = DeployStrategy.STANDARD
    config.automerge = False
    config.promoter_managed_standard = True
    return config


def _std_plan():
    plan = Mock()
    plan.strategy = UpdateStrategy.PRODUCTION
    plan.multi_stage = False
    plan.helm_chart = "dummy-service"
    plan.image_tag = "production-abc123"
    return plan


def test_standard_2wave_dev_is_wave0_prod_is_wave1():
    changes = [_stack_change(s) for s in DEV_STACKS + PROD_STACKS]
    groups = _group_changes_standard_2wave(changes, _std_plan(), _std_config(), Mock())

    assert len(groups) == 2
    by_wave = {g["wave_number"]: g for g in groups}
    assert set(by_wave) == {0, 1}

    # Wave 0 = exactly the dev stacks (anchor).
    assert sorted(by_wave[0]["stacks"]) == sorted(DEV_STACKS)
    # Wave 1 = exactly the prod stacks.
    assert sorted(by_wave[1]["stacks"]) == sorted(PROD_STACKS)


def test_standard_2wave_labels_are_deploy_standard_plus_wave():
    changes = [_stack_change(s) for s in DEV_STACKS + PROD_STACKS]
    groups = _group_changes_standard_2wave(changes, _std_plan(), _std_config(), Mock())
    by_wave = {g["wave_number"]: g for g in groups}

    assert by_wave[0]["labels"] == ["release:wave:0", "deploy:standard"]
    assert by_wave[1]["labels"] == ["release:wave:1", "deploy:standard"]
    for g in groups:
        assert g["pr_type"] == "wave"
        assert g["base_branch"] == "main"
        assert not any(l.startswith("release:id:") for l in g["labels"])


def test_standard_2wave_no_cloud_dimension():
    # 3 clouds of dev + 3 clouds of prod must NOT fan out per-cloud: still exactly 2 PRs.
    changes = [_stack_change(s) for s in DEV_STACKS + PROD_STACKS]
    groups = _group_changes_standard_2wave(changes, _std_plan(), _std_config(), Mock())
    assert len(groups) == 2
    for g in groups:
        assert "cloud_provider" not in g


def test_standard_2wave_excludes_e2e_stacks():
    changes = [_stack_change(s) for s in DEV_STACKS + PROD_STACKS]
    changes.append(_stack_change("foo-bar-e2e"))
    groups = _group_changes_standard_2wave(changes, _std_plan(), _std_config(), Mock())
    all_stacks = [s for g in groups for s in g["stacks"]]
    assert "foo-bar-e2e" not in all_stacks


def test_standard_1wave_fallback_no_dev_stacks():
    # No dev stacks → single wave 0 = prod (degenerates to straight-to-prod).
    changes = [_stack_change(s) for s in PROD_STACKS]
    groups = _group_changes_standard_2wave(changes, _std_plan(), _std_config(), Mock())

    assert len(groups) == 1
    g0 = groups[0]
    assert g0["wave_number"] == 0
    assert sorted(g0["stacks"]) == sorted(PROD_STACKS)
    assert g0["labels"] == ["release:wave:0", "deploy:standard"]


def test_standard_1wave_fallback_no_prod_stacks():
    # No prod stacks → single wave 0 = dev.
    changes = [_stack_change(s) for s in DEV_STACKS]
    groups = _group_changes_standard_2wave(changes, _std_plan(), _std_config(), Mock())

    assert len(groups) == 1
    g0 = groups[0]
    assert g0["wave_number"] == 0
    assert sorted(g0["stacks"]) == sorted(DEV_STACKS)
    assert g0["labels"] == ["release:wave:0", "deploy:standard"]


# --- routing: _group_changes_for_prs dispatches to the standard 2-wave branch -------


def test_group_changes_for_prs_routes_explicit_standard_unmerged_to_2wave():
    changes = [_stack_change(s) for s in DEV_STACKS + PROD_STACKS]
    groups = _group_changes_for_prs(changes, _std_plan(), _std_config(), Mock())
    by_wave = {g["wave_number"]: g for g in groups}
    assert set(by_wave) == {0, 1}
    assert sorted(by_wave[0]["stacks"]) == sorted(DEV_STACKS)
    assert sorted(by_wave[1]["stacks"]) == sorted(PROD_STACKS)
    assert by_wave[0]["labels"] == ["release:wave:0", "deploy:standard"]


def test_group_changes_for_prs_legacy_default_standard_automerge_unchanged():
    # Default standard + automerge=true (production tag, multiple stacks) → ONE legacy PR,
    # NOT the 2-wave promoter path.
    config = Mock()
    config.deploy_strategy = DeployStrategy.STANDARD
    config.automerge = True
    config.promoter_managed_standard = False  # default standard, not opted in
    plan = _std_plan()
    changes = [_stack_change(s) for s in PROD_STACKS]

    groups = _group_changes_for_prs(changes, plan, config, Mock())
    assert len(groups) == 1
    assert groups[0]["pr_type"] == "standard"
    assert groups[0].get("wave_number") is None
    assert groups[0].get("labels", []) == []


def test_group_changes_for_prs_legacy_standard_automerge_false_per_stack_unchanged():
    # Default standard + automerge=false on a multi-stack production deploy → one PR
    # per stack (legacy), NOT a 2-wave release. Gated off because deploy_strategy is the
    # DEFAULT (no explicit standard signal) — see plan_builder routing.
    config = Mock()
    config.deploy_strategy = DeployStrategy.STANDARD
    config.automerge = False
    config.promoter_managed_standard = False  # not explicitly opted in
    plan = _std_plan()
    changes = [_stack_change(s) for s in PROD_STACKS]

    groups = _group_changes_for_prs(changes, plan, config, Mock())
    # Legacy per-stack PRs: one per stack, no wave labels.
    assert len(groups) == len(PROD_STACKS)
    for g in groups:
        assert g["pr_type"] == "standard"
        assert g.get("wave_number") is None


def test_group_changes_for_prs_override_not_hijacked_by_standard():
    # ST-4126 routing guard: explicit standard + automerge=false but an OVERRIDE
    # deploy must stay the override single-PR — the 2-wave standard path is for
    # full PRODUCTION/DEV deploys only, never override.
    config = _std_config()  # promoter_managed_standard=True
    plan = _std_plan()
    plan.strategy = UpdateStrategy.OVERRIDE
    changes = [_stack_change("kbc-us-east-1")]

    groups = _group_changes_for_prs(changes, plan, config, Mock())
    assert len(groups) == 1
    assert groups[0]["pr_type"] == "standard"
    assert groups[0].get("wave_number") is None
    assert groups[0].get("labels", []) == []


def test_group_changes_for_prs_canary_not_hijacked_by_standard():
    # ST-4126 routing guard: a CANARY tag must stay a canary deploy even when
    # standard + automerge=false is set — the standard 2-wave path must NOT preempt it.
    config = _std_config()
    config.image_tag = "canary-orion-abc123"
    plan = _std_plan()
    plan.strategy = UpdateStrategy.CANARY
    plan.image_tag = "canary-orion-abc123"
    changes = [_stack_change(s) for s in DEV_STACKS + PROD_STACKS]

    groups = _group_changes_for_prs(changes, plan, config, Mock())
    assert len(groups) == 1
    assert groups[0]["pr_type"] == "canary"
    assert all(g.get("wave_number") is None for g in groups)


def test_canary_auto_merges_regardless_of_automerge_flag():
    # ST-4126 rollout safety: canary must merge RIGHT AWAY via HIU regardless of the
    # automerge flag. The promoter only discovers `release:wave:0` anchors on
    # kbc-stacks@main, while a canary PR targets the canary-* branch — so if HIU did NOT
    # auto-merge it, the canary deploy would never land. This MUST stay true even once the
    # standard rollout (ST-4131) makes automerge=false the default.
    plan = Mock()
    plan.strategy = UpdateStrategy.CANARY
    assert _should_auto_merge(plan, "canary", user_requested=False) is True
    assert _should_auto_merge(plan, "canary", user_requested=True) is True


# --- prepare_plan: manifest-context + idempotency-guard wiring (integration) -------


def _make_tag_yaml(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("image:\n  tag: old-tag\n")


@pytest.fixture
def std_stacks(tmp_path):
    """One real dev stack + one real prod stack on disk for a `test-chart` deploy."""
    dev = tmp_path / "dev-keboola-gcp-us-central1"   # dev (DEV_STACK_MAPPING)
    prod = tmp_path / "kbc-us-east-1"                 # prod
    _make_tag_yaml(dev / "test-chart" / "tag.yaml")
    _make_tag_yaml(prod / "test-chart" / "tag.yaml")
    return {"base_dir": tmp_path, "dev": dev, "prod": prod}


def _io_layer():
    io = IOLayer(Mock(), Mock(), dry_run=True, approve_github_repo=Mock())
    return io


def test_prepare_plan_standard_sets_manifest_context_and_two_waves(std_stacks):
    os.chdir(std_stacks["base_dir"])
    env = {
        "HELM_CHART": "test-chart",
        "IMAGE_TAG": "production-abc123",
        "GH_TOKEN": "t",
        "GH_APPROVE_TOKEN": "a",
        "DEPLOY_STRATEGY": "standard",
        "AUTOMERGE": "false",
        "DRY_RUN": "true",
        "TARGET_PATH": str(std_stacks["base_dir"]),
    }
    config = EnvironmentConfig.from_env(env)
    assert config.validate() == []
    assert config.promoter_managed_standard is True

    plan = prepare_plan(config, _io_layer())

    # Manifest context derived (so the executor can patch the wave-0 anchor).
    assert plan.manifest_context is not None
    assert plan.manifest_context["app"] == "test-chart"
    assert plan.manifest_context["instance_id"].startswith("test-chart-")

    # Two wave PRs (dev=0, prod=1), unmerged, labelled.
    assert len(plan.pr_plans) == 2
    by_wave = {p.wave_number: p for p in plan.pr_plans}
    assert set(by_wave) == {0, 1}
    assert by_wave[0].auto_merge is False
    assert by_wave[1].auto_merge is False
    assert by_wave[0].labels == ["release:wave:0", "deploy:standard"]
    assert by_wave[1].labels == ["release:wave:1", "deploy:standard"]


def test_prepare_plan_explicit_standard_ignores_automerge_true(std_stacks):
    # ST-4126: AUTOMERGE is IGNORED for an EXPLICIT DEPLOY_STRATEGY=standard (same as the
    # wave strategies). AUTOMERGE=true must NOT revert it to a legacy single-PR deploy — it
    # still emits the promoter-managed 2-wave release with unmerged wave PRs (which HIU
    # auto-approves because auto_merge=False, so release-promoter can merge them).
    os.chdir(std_stacks["base_dir"])
    env = {
        "HELM_CHART": "test-chart",
        "IMAGE_TAG": "production-abc123",
        "GH_TOKEN": "t",
        "GH_APPROVE_TOKEN": "a",
        "DEPLOY_STRATEGY": "standard",
        "AUTOMERGE": "true",  # explicitly true — must be ignored for explicit standard
        "DRY_RUN": "true",
        "TARGET_PATH": str(std_stacks["base_dir"]),
    }
    config = EnvironmentConfig.from_env(env)
    assert config.promoter_managed_standard is True

    plan = prepare_plan(config, _io_layer())
    assert plan.manifest_context is not None
    assert len(plan.pr_plans) == 2
    by_wave = {p.wave_number: p for p in plan.pr_plans}
    assert set(by_wave) == {0, 1}
    assert by_wave[0].auto_merge is False
    assert by_wave[1].auto_merge is False


def test_prepare_plan_standard_invokes_idempotency_guard(std_stacks):
    # A non-dry-run with an already-open anchor carrying this instanceId must raise.
    os.chdir(std_stacks["base_dir"])
    env = {
        "HELM_CHART": "test-chart",
        "IMAGE_TAG": "production-abc123",
        "GH_TOKEN": "t",
        "GH_APPROVE_TOKEN": "a",
        "DEPLOY_STRATEGY": "standard",
        "AUTOMERGE": "false",
        "DRY_RUN": "false",
        "TARGET_PATH": str(std_stacks["base_dir"]),
        "METADATA": __import__("base64").b64encode(
            __import__("json").dumps({"source": {"sha": "deadbeef0123abc"}}).encode()
        ).decode(),
    }
    config = EnvironmentConfig.from_env(env)
    assert config.promoter_managed_standard is True

    from helm_image_updater.manifest import build_manifest, manifest_block, compute_instance_id

    iid = compute_instance_id("test-chart", "deadbeef0123abc", "production-abc123")
    anchor_body = manifest_block(build_manifest(
        app="test-chart", instance_id=iid, display_name="test-chart@production-abc123",
        waves={0: 9, 1: 10},
    ))

    io = IOLayer(Mock(), Mock(), dry_run=False, approve_github_repo=Mock())
    io.find_open_release_anchors = Mock(return_value=[(9, anchor_body)])

    with pytest.raises(RuntimeError, match="already has an open anchor"):
        prepare_plan(config, io)


def test_prepare_plan_legacy_standard_no_manifest_context(std_stacks):
    # Default (empty) standard + automerge=true → legacy single PR, NO manifest context.
    os.chdir(std_stacks["base_dir"])
    env = {
        "HELM_CHART": "test-chart",
        "IMAGE_TAG": "production-abc123",
        "GH_TOKEN": "t",
        "GH_APPROVE_TOKEN": "a",
        "DEPLOY_STRATEGY": "",
        "AUTOMERGE": "true",
        "DRY_RUN": "true",
        "TARGET_PATH": str(std_stacks["base_dir"]),
    }
    config = EnvironmentConfig.from_env(env)
    assert config.promoter_managed_standard is False

    plan = prepare_plan(config, _io_layer())
    assert plan.manifest_context is None
    # legacy single PR for all stacks
    assert len(plan.pr_plans) == 1
    assert plan.pr_plans[0].wave_number is None


def test_prepare_plan_explicit_standard_override_stack_not_managed(std_stacks):
    # ST-4126 (Copilot review): an explicit DEPLOY_STRATEGY=standard with an OVERRIDE-STACK
    # deploy must NOT be promoter-managed — no manifest_context and no idempotency guard,
    # just the override single-PR. The manifest/guard wiring must be gated on plan.strategy
    # the same way the grouping is (canary/override are orthogonal axes).
    os.chdir(std_stacks["base_dir"])
    env = {
        "HELM_CHART": "test-chart",
        "IMAGE_TAG": "production-abc123",
        "GH_TOKEN": "t",
        "GH_APPROVE_TOKEN": "a",
        "DEPLOY_STRATEGY": "standard",
        "OVERRIDE_STACK": "kbc-us-east-1",  # a real prod stack on disk → plan.strategy = OVERRIDE
        "DRY_RUN": "true",
        "TARGET_PATH": str(std_stacks["base_dir"]),
    }
    config = EnvironmentConfig.from_env(env)
    plan = prepare_plan(config, _io_layer())

    assert plan.strategy == UpdateStrategy.OVERRIDE
    assert plan.manifest_context is None            # NOT promoter-managed → no manifest
    assert len(plan.pr_plans) == 1                  # override single-PR, not 2-wave
    assert plan.pr_plans[0].wave_number is None
    assert plan.pr_plans[0].labels == []


def test_prepare_plan_explicit_standard_dev_tag_not_managed(std_stacks):
    # F2 (Halama review): a dev-* tag is NOT a production release. Even with an explicit
    # DEPLOY_STRATEGY=standard it must NOT enter the promoter-managed 2-wave path — that
    # would make the dev deploy an unmerged wave PR + manifest + arm the idempotency guard,
    # stranding the dev update (the promoter only merges release:wave:0 anchors). The gate
    # is PRODUCTION-only; dev tags keep their legacy fast (auto-merged) behavior.
    os.chdir(std_stacks["base_dir"])
    env = {
        "HELM_CHART": "test-chart",
        "IMAGE_TAG": "dev-abc123",
        "GH_TOKEN": "t",
        "GH_APPROVE_TOKEN": "a",
        "DEPLOY_STRATEGY": "standard",
        "DRY_RUN": "true",
        "TARGET_PATH": str(std_stacks["base_dir"]),
    }
    config = EnvironmentConfig.from_env(env)
    plan = prepare_plan(config, _io_layer())

    assert plan.strategy == UpdateStrategy.DEV
    assert plan.manifest_context is None             # NOT promoter-managed
    assert all(p.wave_number is None for p in plan.pr_plans)  # no wave PRs (legacy dev grouping)


def test_standard_2wave_prod_wave_uses_positive_is_production():
    # F1 (Halama review): the prod wave is the POSITIVE is_production set, NOT "not is_dev".
    # A canary stack (is_dev=False AND is_production=False) must be DROPPED, never mis-binned
    # into the prod wave by negation.
    changes = [_stack_change(s) for s in DEV_STACKS + PROD_STACKS]
    changes.append(_stack_change("dev-keboola-canary-orion"))  # canary: not dev, not prod
    groups = _group_changes_standard_2wave(changes, _std_plan(), _std_config(), Mock())
    by_wave = {g["wave_number"]: g for g in groups}

    assert "dev-keboola-canary-orion" not in by_wave[0]["stacks"]
    assert "dev-keboola-canary-orion" not in by_wave[1]["stacks"]
    assert sorted(by_wave[1]["stacks"]) == sorted(PROD_STACKS)  # only the real prod stacks
