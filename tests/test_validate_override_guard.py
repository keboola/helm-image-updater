"""validate() guard (ST-4169): a PRODUCTION override stack rejects any non-production
tag -- dev-/canary- and unrecognized/`pr-test-*` (INVALID) -- including via extra tags.
Non-production override targets (e.g. e2e) stay unrestricted."""

from helm_image_updater.environment import EnvironmentConfig


def _cfg(**kw):
    base = dict(helm_chart="x", github_token="t", approve_token="a")
    base.update(kw)
    return EnvironmentConfig(**base)


def test_canary_tag_blocked_on_prod_override(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "prod-stack").mkdir()  # unknown name -> classify_stack -> is_production
    cfg = _cfg(image_tag="canary-orion-abc", override_stack="prod-stack")
    assert any("non-production" in e for e in cfg.validate())


def test_dev_extra_tag_blocked_on_prod_override(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "prod-stack").mkdir()
    cfg = _cfg(
        image_tag="production-abc",
        override_stack="prod-stack",
        extra_tags=[{"path": "a.tag", "value": "dev-xyz"}],
    )
    assert any("non-production" in e for e in cfg.validate())


def test_pr_test_invalid_tag_blocked_on_prod_override(tmp_path, monkeypatch):
    # Tag-format validation is skipped in override mode, so a pr-test-* (INVALID) tag
    # reaches the guard; a production override target must still reject it (Copilot #43).
    monkeypatch.chdir(tmp_path)
    (tmp_path / "prod-stack").mkdir()
    cfg = _cfg(image_tag="pr-test-7786-abc", override_stack="prod-stack")
    assert any("non-production" in e for e in cfg.validate())


def test_dev_tag_allowed_on_e2e_override(tmp_path, monkeypatch):
    # e2e stacks are EXCLUDED_STACKS (not production) -> dev/pr-test deploys are allowed.
    monkeypatch.chdir(tmp_path)
    (tmp_path / "dev-keboola-gcp-us-east1-e2e").mkdir()
    cfg = _cfg(image_tag="dev-abc", override_stack="dev-keboola-gcp-us-east1-e2e")
    assert not any("non-production" in e for e in cfg.validate())
