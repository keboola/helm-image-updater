"""ST-4126: promoter-managed `standard` deploy = 2-wave dev→prod release.

Wave 0 = all dev stacks (anchor, carries the manifest), wave 1 = all prod stacks.
The cloud dimension is collapsed (no per-cloud split). Activation: explicit
DEPLOY_STRATEGY=standard + automerge=false on a production/semver tag.
"""

from unittest.mock import Mock

import pytest

from helm_image_updater.models import UpdateStrategy, DeployStrategy
from helm_image_updater.plan_builder import (
    _group_changes_for_prs,
    _group_changes_standard_2wave,
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
