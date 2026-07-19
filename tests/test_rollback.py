"""Tests for the `rollback` DEPLOY_STRATEGY (ST-4277, Task B1)
and the rollback instanceId computation (ST-4277, Task B2).

Mirrors the fixtures/helpers used in tests/test_deploy_strategy.py.
"""

from helm_image_updater.environment import EnvironmentConfig
from helm_image_updater.manifest import compute_rollback_instance_id
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
