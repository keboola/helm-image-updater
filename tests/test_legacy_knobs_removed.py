"""ST-4159: legacy knobs are gone — AUTOMERGE/MULTI_STAGE env ignored,
cloud_multi_stage invalid, empty strategy resolves to standard."""
from helm_image_updater.environment import EnvironmentConfig
from helm_image_updater.models import DeployStrategy

BASE = {"HELM_CHART": "x", "IMAGE_TAG": "production-abc",
        "GH_TOKEN": "t", "GH_APPROVE_TOKEN": "a"}


def test_automerge_env_not_parsed():
    cfg = EnvironmentConfig.from_env({**BASE, "AUTOMERGE": "false"})
    assert not hasattr(cfg, "automerge")


def test_multi_stage_env_ignored_with_warning(capsys):
    cfg = EnvironmentConfig.from_env({**BASE, "MULTI_STAGE": "true"})
    assert not hasattr(cfg, "multi_stage")
    assert cfg.deploy_strategy == DeployStrategy.STANDARD
    assert "MULTI_STAGE is deprecated" in capsys.readouterr().out


def test_empty_strategy_is_standard():
    cfg = EnvironmentConfig.from_env(BASE)
    assert cfg.deploy_strategy == DeployStrategy.STANDARD
    assert not hasattr(cfg, "promoter_managed_standard")


def test_cloud_multi_stage_is_invalid_strategy():
    cfg = EnvironmentConfig.from_env({**BASE, "DEPLOY_STRATEGY": "cloud_multi_stage"})
    assert any("Invalid DEPLOY_STRATEGY" in e for e in cfg.validate())
