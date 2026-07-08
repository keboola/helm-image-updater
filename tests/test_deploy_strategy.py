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


def test_explicit_standard_overrides_multi_stage_flag():
    # DEPLOY_STRATEGY=standard wins over MULTI_STAGE=true: multi_stage must be False.
    cfg = EnvironmentConfig.from_env(_base_env(DEPLOY_STRATEGY="standard", MULTI_STAGE="true"))
    assert cfg.deploy_strategy == DeployStrategy.STANDARD
    assert cfg.multi_stage is False


def test_wave_strategy_ok_with_production_extra_tag_only():
    # Extra-tags-only production rollout (empty IMAGE_TAG, production EXTRA_TAG) is valid:
    # the manifest identity and stack selection already support it. Regression for the
    # job-queue-daemon jobQueueRunnerImage.tag production deploy that failed validation.
    cfg = EnvironmentConfig.from_env({
        "HELM_CHART": "dummy-service", "GH_TOKEN": "t", "GH_APPROVE_TOKEN": "a",
        "EXTRA_TAG1": "jobQueueRunnerImage.tag:production-abc", "DEPLOY_STRATEGY": "critical-manual-gate",
    })
    assert cfg.validate() == []


def test_wave_strategy_rejects_dev_extra_tag_only():
    # A dev tag (even via EXTRA_TAG) cannot drive a production wave rollout.
    cfg = EnvironmentConfig.from_env({
        "HELM_CHART": "dummy-service", "GH_TOKEN": "t", "GH_APPROVE_TOKEN": "a",
        "EXTRA_TAG1": "image.tag:dev-abc", "DEPLOY_STRATEGY": "gradual",
    })
    errors = cfg.validate()
    assert any("requires a production" in e for e in errors)


def test_manual_per_stack_ok_with_production_extra_tag_only():
    cfg = EnvironmentConfig.from_env({
        "HELM_CHART": "dummy-service", "GH_TOKEN": "t", "GH_APPROVE_TOKEN": "a",
        "EXTRA_TAG1": "image.tag:production-abc", "DEPLOY_STRATEGY": "manual-per-stack",
    })
    assert cfg.validate() == []


def test_manual_per_stack_rejects_dev_extra_tag_only():
    cfg = EnvironmentConfig.from_env({
        "HELM_CHART": "dummy-service", "GH_TOKEN": "t", "GH_APPROVE_TOKEN": "a",
        "EXTRA_TAG1": "image.tag:dev-abc", "DEPLOY_STRATEGY": "manual-per-stack",
    })
    errors = cfg.validate()
    assert any("requires a production" in e for e in errors)


# Task 5: resolve_wave
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


def test_resolve_wave_rejects_non_integer():
    import pytest
    with pytest.raises(ValueError):
        resolve_wave("kbc-us-east-1", {"rollout_wave": 1.9})
    with pytest.raises(ValueError):
        resolve_wave("kbc-us-east-1", {"rollout_wave": True})


def test_promoter_managed_standard_requires_explicit_standard_and_automerge_false():
    # Explicit DEPLOY_STRATEGY=standard + automerge=false → promoter-managed 2-wave.
    cfg = EnvironmentConfig.from_env(_base_env(DEPLOY_STRATEGY="standard", AUTOMERGE="false"))
    assert cfg.promoter_managed_standard is True
    assert cfg.deploy_strategy == DeployStrategy.STANDARD
    assert cfg.multi_stage is False


def test_promoter_managed_standard_ignores_automerge_for_explicit_standard():
    # Explicit standard → promoter-managed 2-wave regardless of AUTOMERGE (ST-4126):
    # AUTOMERGE is ignored, exactly like the wave strategies.
    for automerge in ("true", "false"):
        cfg = EnvironmentConfig.from_env(_base_env(DEPLOY_STRATEGY="standard", AUTOMERGE=automerge))
        assert cfg.promoter_managed_standard is True, f"automerge={automerge}"


def test_promoter_managed_standard_off_for_default_empty_strategy():
    # Empty DEPLOY_STRATEGY (the action default) collapses to STANDARD but is NOT explicit;
    # even with automerge=false it must stay the legacy per-stack path.
    cfg = EnvironmentConfig.from_env(_base_env(DEPLOY_STRATEGY="", AUTOMERGE="false"))
    assert cfg.deploy_strategy == DeployStrategy.STANDARD
    assert cfg.promoter_managed_standard is False


def test_promoter_managed_standard_off_for_wave_strategies():
    cfg = EnvironmentConfig.from_env(_base_env(DEPLOY_STRATEGY="gradual", AUTOMERGE="false"))
    assert cfg.promoter_managed_standard is False


def test_empty_deploy_strategy_with_multi_stage_aliases_to_cloud_multi_stage():
    # The action passes deploy-strategy='' by default; empty must behave as unset
    # so MULTI_STAGE=true still aliases to cloud_multi_stage (legacy action callers).
    cfg = EnvironmentConfig.from_env(_base_env(DEPLOY_STRATEGY="", MULTI_STAGE="true"))
    assert cfg.deploy_strategy == DeployStrategy.CLOUD_MULTI_STAGE
    assert cfg.multi_stage is True
