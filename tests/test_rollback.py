"""Tests for the `rollback` DEPLOY_STRATEGY (ST-4277, Task B1),
the rollback instanceId computation (ST-4277, Task B2), and the rollback
plan itself -- grouping, PR plan, manifest wiring, zero-diff guard (Task B3).

Mirrors the fixtures/helpers used in tests/test_deploy_strategy.py and the
grouping/PR-plan style of tests/test_standard_2wave.py.
"""

import base64
import json
import os
from unittest.mock import Mock

import pytest

from helm_image_updater.environment import EnvironmentConfig
from helm_image_updater.manifest import compute_rollback_instance_id
from helm_image_updater.models import DeployStrategy, UpdateStrategy
from helm_image_updater.io_layer import IOLayer
from helm_image_updater.plan_builder import (
    prepare_plan,
    _group_changes_for_prs,
    _build_manifest_context,
    _create_pr_plan,
    _guard_release_not_already_open,
)
from helm_image_updater.wave_planning import wave_label, deploy_label


def _base_env(**overrides):
    env = {
        "HELM_CHART": "dummy-service",
        "IMAGE_TAG": "production-abc123",
        "GH_TOKEN": "t",
        "GH_APPROVE_TOKEN": "a",
    }
    env.update(overrides)
    return env


def test_rollback_enum_parses():
    assert DeployStrategy("rollback") is DeployStrategy.ROLLBACK
    assert not DeployStrategy.ROLLBACK.is_wave
    assert DeployStrategy.ROLLBACK.is_promoter_managed


def test_deploy_strategy_parses_rollback():
    cfg = EnvironmentConfig.from_env(_base_env(DEPLOY_STRATEGY="rollback"))
    assert cfg.deploy_strategy == DeployStrategy.ROLLBACK


def test_rollback_ok_with_production_tag():
    cfg = EnvironmentConfig.from_env(_base_env(IMAGE_TAG="production-abc", DEPLOY_STRATEGY="rollback"))
    assert cfg.validate() == []


def test_rollback_requires_production_tag():
    cfg = EnvironmentConfig.from_env(_base_env(IMAGE_TAG="dev-abc", DEPLOY_STRATEGY="rollback"))
    errors = cfg.validate()
    assert any("requires a production" in e for e in errors)


def test_rollback_rejects_override_stack():
    cfg = EnvironmentConfig.from_env(
        _base_env(DEPLOY_STRATEGY="rollback", OVERRIDE_STACK="kbc-us-east-1")
    )
    errors = cfg.validate()
    assert any("OVERRIDE_STACK" in e for e in errors)


def test_rollback_ok_with_production_extra_tag_only():
    cfg = EnvironmentConfig.from_env({
        "HELM_CHART": "dummy-service", "GH_TOKEN": "t", "GH_APPROVE_TOKEN": "a",
        "EXTRA_TAG1": "image.tag:production-abc", "DEPLOY_STRATEGY": "rollback",
    })
    assert cfg.validate() == []


def test_rollback_rejects_dev_extra_tag_only():
    cfg = EnvironmentConfig.from_env({
        "HELM_CHART": "dummy-service", "GH_TOKEN": "t", "GH_APPROVE_TOKEN": "a",
        "EXTRA_TAG1": "image.tag:dev-abc", "DEPLOY_STRATEGY": "rollback",
    })
    errors = cfg.validate()
    assert any("requires a production" in e for e in errors)


# ---------------------------------------------------------------------------
# compute_rollback_instance_id (ST-4277, Task B2)
# ---------------------------------------------------------------------------

def test_rollback_instance_id_shape():
    # <app>-rollback-<sig>-<run_id>, sig = the ST-4190 compute_instance_id signature
    # (tag/extras) minus the app prefix. Distinct from the plain <app>-<tag> id so the
    # rollback release never collides with the original release's instanceId.
    assert compute_rollback_instance_id("connection", "production-1.2.3", [], "16234567890") \
        == "connection-rollback-production-1.2.3-16234567890"


def test_rollback_instance_id_extras_only():
    # Empty tag (extra-tags-only rollback) folds the extras exactly like compute_instance_id.
    rid = compute_rollback_instance_id("connection", "", [{"path": "a.b", "value": "1"}], "99")
    assert rid == "connection-rollback-a.b=1-99"


def test_rollback_instance_id_idempotent_same_run_id():
    # A re-run of the SAME workflow run (same run_id) keeps the same id.
    a = compute_rollback_instance_id("connection", "production-1.2.3", [], "111")
    b = compute_rollback_instance_id("connection", "production-1.2.3", [], "111")
    assert a == b


def test_rollback_instance_id_distinct_per_run_id():
    # A fresh dispatch (different run_id) gets a new id, even for the same payload.
    a = compute_rollback_instance_id("connection", "production-1.2.3", [], "111")
    b = compute_rollback_instance_id("connection", "production-1.2.3", [], "222")
    assert a != b


# ---------------------------------------------------------------------------
# Task B3: the rollback plan -- grouping, PR plan, manifest wiring, guard,
# zero-diff error.
# ---------------------------------------------------------------------------


def _stack_change(stack):
    return {"stack": stack, "file_change": Mock(), "changes": []}


def _rollback_config():
    config = Mock()
    config.deploy_strategy = DeployStrategy.ROLLBACK
    return config


def _rollback_plan(**overrides):
    plan = Mock()
    plan.strategy = UpdateStrategy.PRODUCTION
    plan.helm_chart = "dummy-service"
    plan.image_tag = "production-rollback-tag"
    plan.extra_tags = []
    plan.metadata = {}
    for k, v in overrides.items():
        setattr(plan, k, v)
    return plan


# --- (a) grouping: ONE wave-0 PR over every changed stack, labelled ----------------


def test_rollback_grouping_is_one_pr_wave0_all_stacks():
    stacks = ["dev1", "prod1", "prod2"]
    changes = [_stack_change(s) for s in stacks]
    groups = _group_changes_for_prs(changes, _rollback_plan(), _rollback_config(), Mock())

    assert len(groups) == 1
    group = groups[0]
    assert group["pr_type"] == "wave"
    assert group["wave_number"] == 0
    assert group["labels"] == [wave_label(0), deploy_label(DeployStrategy.ROLLBACK)]
    assert group["labels"] == ["release:wave:0", "deploy:rollback"]
    assert sorted(group["stacks"]) == sorted(stacks)
    assert group["base_branch"] == "main"


def test_rollback_grouping_hard_raises_on_non_production_target():
    # Defensive: B1 already rejects a non-production rollback at the env layer, but the
    # grouping branch must ALSO hard-raise -- a silent fall-through to the auto-merged
    # DEV/OVERRIDE tail must be impossible.
    plan = _rollback_plan(strategy=UpdateStrategy.DEV)
    changes = [_stack_change("dev1")]
    with pytest.raises(RuntimeError, match="requires a production"):
        _group_changes_for_prs(changes, plan, _rollback_config(), Mock())


def test_rollback_grouping_is_first_branch_before_is_wave():
    # config.deploy_strategy.is_wave is False for ROLLBACK (models.py), so this also
    # proves the rollback branch does not depend on is_wave routing at all.
    assert DeployStrategy.ROLLBACK.is_wave is False
    stacks = ["dev1", "prod1", "prod2"]
    changes = [_stack_change(s) for s in stacks]
    groups = _group_changes_for_prs(changes, _rollback_plan(), _rollback_config(), Mock())
    assert len(groups) == 1
    assert groups[0]["wave_number"] == 0


# --- (b) PR plan: no auto-merge, ⏪ ROLLBACK title, reason in body -----------------


def test_rollback_pr_plan_never_auto_merges_and_has_rollback_title():
    plan = _rollback_plan()
    config = _rollback_config()
    pr_group = {
        "stacks": ["dev1", "prod1", "prod2"],
        "changes": [_stack_change(s) for s in ["dev1", "prod1", "prod2"]],
        "base_branch": "main",
        "pr_type": "wave",
        "wave_number": 0,
        "labels": [wave_label(0), deploy_label(DeployStrategy.ROLLBACK)],
    }
    pr_plan = _create_pr_plan(pr_group, plan, config)

    assert pr_plan.auto_merge is False
    assert pr_plan.pr_title.startswith("⏪ ROLLBACK")
    assert plan.helm_chart in pr_plan.pr_title
    assert plan.image_tag in pr_plan.pr_title


def test_rollback_pr_body_contains_reason_when_set():
    plan = _rollback_plan(metadata={"source": {"reason": "prod-1.2.3 broke checkout"}})
    config = _rollback_config()
    pr_group = {
        "stacks": ["dev1", "prod1", "prod2"],
        "changes": [_stack_change(s) for s in ["dev1", "prod1", "prod2"]],
        "base_branch": "main",
        "pr_type": "wave",
        "wave_number": 0,
        "labels": [wave_label(0), deploy_label(DeployStrategy.ROLLBACK)],
    }
    pr_plan = _create_pr_plan(pr_group, plan, config)

    assert "prod-1.2.3 broke checkout" in pr_plan.pr_body
    assert "**Reason:**" in pr_plan.pr_body


def test_rollback_pr_body_omits_reason_when_absent():
    plan = _rollback_plan(metadata={})
    config = _rollback_config()
    pr_group = {
        "stacks": ["dev1"],
        "changes": [_stack_change("dev1")],
        "base_branch": "main",
        "pr_type": "wave",
        "wave_number": 0,
        "labels": [wave_label(0), deploy_label(DeployStrategy.ROLLBACK)],
    }
    pr_plan = _create_pr_plan(pr_group, plan, config)

    assert "**Reason:**" not in pr_plan.pr_body


# --- (c) manifest context: rollback display_name/instance_id + image_tag/extra_tags


def test_rollback_manifest_context_shape(monkeypatch):
    monkeypatch.setenv("GITHUB_RUN_ID", "16234567890")
    plan = _rollback_plan(
        helm_chart="connection",
        image_tag="production-1.2.3",
        extra_tags=[],
        metadata={"source": {"pr_author": "zajca"}},
    )
    config = _rollback_config()

    ctx = _build_manifest_context(plan, config)

    assert ctx["display_name"] == "ROLLBACK connection → production-1.2.3"
    assert ctx["instance_id"] == compute_rollback_instance_id(
        "connection", "production-1.2.3", [], "16234567890"
    )
    assert ctx["source_pr_author"] == "zajca"
    assert ctx["image_tag"] == "production-1.2.3"
    assert ctx["extra_tags"] == []


def test_rollback_manifest_context_falls_back_to_local_run_id(monkeypatch):
    monkeypatch.delenv("GITHUB_RUN_ID", raising=False)
    plan = _rollback_plan(helm_chart="connection", image_tag="production-1.2.3", extra_tags=[])
    config = _rollback_config()

    ctx = _build_manifest_context(plan, config)

    assert ctx["instance_id"] == compute_rollback_instance_id(
        "connection", "production-1.2.3", [], "local"
    )


def test_non_rollback_manifest_context_carries_image_tag_and_extra_tags():
    # (ALL strategies) get the two new context keys, not just rollback.
    plan = Mock()
    plan.helm_chart = "connection"
    plan.image_tag = "production-abc"
    plan.extra_tags = [{"path": "a.b", "value": "1"}]
    plan.metadata = {}

    ctx = _build_manifest_context(plan)  # no config -> non-rollback shape (back-compat)

    assert ctx["image_tag"] == "production-abc"
    assert ctx["extra_tags"] == [{"path": "a.b", "value": "1"}]
    assert ctx["display_name"] == "connection@production-abc"  # unchanged non-rollback shape


# --- (d) zero-diff: rollback-specific error mentions promoter:abandon-release -----


def _make_tag_yaml(path, tag="production-1.2.3"):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"image:\n  tag: {tag}\n")


@pytest.fixture
def rollback_stacks(tmp_path):
    """One real prod stack already on the target tag -> zero diff for a rollback."""
    prod = tmp_path / "kbc-us-east-1"
    _make_tag_yaml(prod / "test-chart" / "tag.yaml", tag="production-1.2.3")
    return {"base_dir": tmp_path, "prod": prod}


def _io_layer(dry_run=True):
    return IOLayer(Mock(), Mock(), dry_run=dry_run, approve_github_repo=Mock())


def test_rollback_zero_diff_raises_abandon_release_guidance(rollback_stacks):
    os.chdir(rollback_stacks["base_dir"])
    env = {
        "HELM_CHART": "test-chart",
        "IMAGE_TAG": "production-1.2.3",  # already the tag on disk -> zero diff
        "GH_TOKEN": "t",
        "GH_APPROVE_TOKEN": "a",
        "DEPLOY_STRATEGY": "rollback",
        "DRY_RUN": "true",
        "TARGET_PATH": str(rollback_stacks["base_dir"]),
    }
    config = EnvironmentConfig.from_env(env)
    assert config.deploy_strategy == DeployStrategy.ROLLBACK

    with pytest.raises(RuntimeError, match="promoter:abandon-release"):
        prepare_plan(config, _io_layer())


def test_non_rollback_zero_diff_keeps_existing_message(rollback_stacks):
    # Non-rollback zero-diff must NOT mention promoter:abandon-release -- the existing
    # noop message is unchanged.
    os.chdir(rollback_stacks["base_dir"])
    env = {
        "HELM_CHART": "test-chart",
        "IMAGE_TAG": "production-1.2.3",  # already the tag on disk -> zero diff
        "GH_TOKEN": "t",
        "GH_APPROVE_TOKEN": "a",
        "DEPLOY_STRATEGY": "standard",
        "DRY_RUN": "true",
        "TARGET_PATH": str(rollback_stacks["base_dir"]),
    }
    config = EnvironmentConfig.from_env(env)

    with pytest.raises(RuntimeError) as exc_info:
        prepare_plan(config, _io_layer())
    assert "promoter:abandon-release" not in str(exc_info.value)
    assert "noop change" in str(exc_info.value)


# --- (e) prepare_plan wires manifest_context + idempotency guard for rollback ------


@pytest.fixture
def rollback_change_stacks(tmp_path):
    """A real prod stack that DIFFERS from the target tag -> a genuine rollback diff."""
    prod = tmp_path / "kbc-us-east-1"
    _make_tag_yaml(prod / "test-chart" / "tag.yaml", tag="production-old")
    return {"base_dir": tmp_path, "prod": prod}


def test_prepare_plan_rollback_sets_manifest_context_one_wave0_pr(rollback_change_stacks):
    os.chdir(rollback_change_stacks["base_dir"])
    env = {
        "HELM_CHART": "test-chart",
        "IMAGE_TAG": "production-new",
        "GH_TOKEN": "t",
        "GH_APPROVE_TOKEN": "a",
        "DEPLOY_STRATEGY": "rollback",
        "DRY_RUN": "true",
        "TARGET_PATH": str(rollback_change_stacks["base_dir"]),
    }
    config = EnvironmentConfig.from_env(env)
    assert config.validate() == []

    plan = prepare_plan(config, _io_layer())

    assert plan.manifest_context is not None
    assert plan.manifest_context["instance_id"].startswith("test-chart-rollback-")
    assert len(plan.pr_plans) == 1
    assert plan.pr_plans[0].wave_number == 0
    assert plan.pr_plans[0].auto_merge is False
    assert plan.pr_plans[0].labels == ["release:wave:0", "deploy:rollback"]


def test_prepare_plan_rollback_invokes_idempotency_guard(rollback_change_stacks, monkeypatch):
    monkeypatch.setenv("GITHUB_RUN_ID", "999")
    os.chdir(rollback_change_stacks["base_dir"])
    env = {
        "HELM_CHART": "test-chart",
        "IMAGE_TAG": "production-new",
        "GH_TOKEN": "t",
        "GH_APPROVE_TOKEN": "a",
        "DEPLOY_STRATEGY": "rollback",
        "DRY_RUN": "false",
        "TARGET_PATH": str(rollback_change_stacks["base_dir"]),
    }
    config = EnvironmentConfig.from_env(env)

    rollback_id = compute_rollback_instance_id("test-chart", "production-new", [], "999")
    from helm_image_updater.manifest import build_manifest, manifest_block

    anchor_body = manifest_block(build_manifest(
        app="test-chart", instance_id=rollback_id, display_name="ROLLBACK test-chart → production-new",
        waves={0: 9},
    ))

    io = IOLayer(Mock(), Mock(), dry_run=False, approve_github_repo=Mock())
    io.find_open_release_anchors = Mock(return_value=[(9, anchor_body)])

    with pytest.raises(RuntimeError, match="already has an open anchor"):
        prepare_plan(config, io)


def test_guard_called_directly_with_rollback_instance_id():
    # Direct unit check that _guard_release_not_already_open works against a rollback
    # id shape the same as any other instanceId (no rollback-specific behavior needed).
    rollback_id = compute_rollback_instance_id("test-chart", "production-new", [], "999")
    io = Mock()
    io.find_open_release_anchors.return_value = [(9, f"```json\n{{\"manifestVersion\": \"v1\", "
                                                       f"\"instanceId\": \"{rollback_id}\", "
                                                       f"\"displayName\": \"x\", \"app\": \"test-chart\", "
                                                       f"\"anchorWave\": 0, \"waves\": {{\"0\": 9}}}}\n```")]
    with pytest.raises(RuntimeError, match="already has an open anchor"):
        _guard_release_not_already_open(rollback_id, io)
