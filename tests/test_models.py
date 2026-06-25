# tests/test_models.py  (add)
from helm_image_updater.models import PRPlan, UpdatePlan, UpdateStrategy, DeployStrategy


def test_is_promoter_managed_includes_wave_strategies_and_standard():
    # Wave strategies are promoter-managed.
    assert DeployStrategy.GRADUAL.is_promoter_managed is True
    assert DeployStrategy.CRITICAL.is_promoter_managed is True
    assert DeployStrategy.CRITICAL_MANUAL_GATE.is_promoter_managed is True
    # STANDARD is promoter-managed too (ST-4126: 2-wave dev→prod when unmerged).
    assert DeployStrategy.STANDARD.is_promoter_managed is True
    # cloud_multi_stage stays legacy, NOT promoter-managed.
    assert DeployStrategy.CLOUD_MULTI_STAGE.is_promoter_managed is False


def test_standard_is_not_is_wave():
    # STANDARD must NOT be routed through _group_changes_by_wave (which hard-requires
    # waves 0..3); only is_promoter_managed includes it.
    assert DeployStrategy.STANDARD.is_wave is False
    assert DeployStrategy.GRADUAL.is_wave is True


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
