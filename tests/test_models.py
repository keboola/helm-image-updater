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
