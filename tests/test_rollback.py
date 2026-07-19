"""Tests for the `rollback` DEPLOY_STRATEGY (ST-4277, Task B1).

Mirrors the fixtures/helpers used in tests/test_deploy_strategy.py.
"""

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
