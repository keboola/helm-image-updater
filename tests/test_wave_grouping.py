"""Wave-mode grouping, auto-merge, labels, idempotency (PR-A)."""

from unittest.mock import Mock
from helm_image_updater.models import UpdateStrategy, DeployStrategy
from helm_image_updater.plan_builder import _group_changes_for_prs


def _stack_change(stack):
    return {"stack": stack, "file_change": Mock(), "changes": []}


def _wave_metadata(by_stack):
    """Return a read_yaml side_effect mapping <stack>/stack-metadata.yaml -> dict."""
    def _read(path):
        for stack, wave in by_stack.items():
            if path == f"{stack}/stack-metadata.yaml":
                return {"rollout_wave": wave}
        return None
    return _read


def test_wave_grouping_one_pr_per_wave_with_labels():
    waves = {
        "dev-keboola-gcp-us-central1": 0,
        "com-keboola-azure-north-europe": 1,
        "kbc-us-east-1": 2,
        "cloud-keboola-cs": 3,
    }
    io = Mock()
    io.read_yaml.side_effect = _wave_metadata(waves)

    config = Mock()
    config.deploy_strategy = DeployStrategy.GRADUAL

    plan = Mock()
    plan.strategy = UpdateStrategy.PRODUCTION
    plan.multi_stage = False
    plan.helm_chart = "dummy-service"
    plan.image_tag = "production-abc123"

    groups = _group_changes_for_prs(
        [_stack_change(s) for s in waves], plan, config, io
    )

    assert len(groups) == 4
    by_wave = {g["wave_number"]: g for g in groups}
    assert set(by_wave) == {0, 1, 2, 3}
    g1 = by_wave[1]
    assert g1["pr_type"] == "wave"
    assert g1["stacks"] == ["com-keboola-azure-north-europe"]
    assert any(l.startswith("release:id:") for l in g1["labels"])
    assert "release:wave:1" in g1["labels"]
    assert "deploy:gradual" in g1["labels"]


def test_wave_grouping_requires_all_waves_0_to_3():
    import pytest
    waves = {"dev-keboola-gcp-us-central1": 0, "kbc-us-east-1": 1}  # missing 2 and 3
    io = Mock()
    io.read_yaml.side_effect = _wave_metadata(waves)
    config = Mock(); config.deploy_strategy = DeployStrategy.GRADUAL
    plan = Mock(); plan.strategy = UpdateStrategy.PRODUCTION; plan.multi_stage = False
    plan.helm_chart = "dummy-service"; plan.image_tag = "production-abc"

    with pytest.raises(RuntimeError, match="wave"):
        _group_changes_for_prs([_stack_change(s) for s in waves], plan, config, io)
