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
