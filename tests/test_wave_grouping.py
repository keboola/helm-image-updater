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
    assert g1["labels"] == ["release:wave:1", "deploy:gradual"]

    # Each wave group carries exactly its wave label + the deploy label (no release:id).
    for g in groups:
        wave = g["wave_number"]
        assert g["labels"] == [f"release:wave:{wave}", "deploy:gradual"], (
            f"Wave {wave} labels {g['labels']} != expected"
        )
        assert not any(l.startswith("release:id:") for l in g["labels"])
        assert "release_id" not in g


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


from helm_image_updater.plan_builder import _should_auto_merge


def test_wave_pr_type_never_auto_merges():
    plan = Mock(); plan.strategy = UpdateStrategy.PRODUCTION
    assert _should_auto_merge(plan, "wave", user_requested=True) is False
    assert _should_auto_merge(plan, "wave", user_requested=False) is False


def test_wave_grouping_rejects_gap():
    """waves {0,1,3} (no wave-2 stack) → RuntimeError."""
    import pytest
    waves = {
        "dev-keboola-gcp-us-central1": 0,
        "com-keboola-azure-north-europe": 1,
        "cloud-keboola-cs": 3,
    }
    io = Mock()
    io.read_yaml.side_effect = _wave_metadata(waves)
    config = Mock(); config.deploy_strategy = DeployStrategy.GRADUAL
    plan = Mock(); plan.strategy = UpdateStrategy.PRODUCTION; plan.multi_stage = False
    plan.helm_chart = "dummy-service"; plan.image_tag = "production-abc"

    with pytest.raises(RuntimeError, match="wave"):
        _group_changes_for_prs([_stack_change(s) for s in waves], plan, config, io)


def test_wave_grouping_rejects_missing_last():
    """waves {0,1,2} (no wave-3 stack) → RuntimeError."""
    import pytest
    waves = {
        "dev-keboola-gcp-us-central1": 0,
        "com-keboola-azure-north-europe": 1,
        "kbc-us-east-1": 2,
    }
    io = Mock()
    io.read_yaml.side_effect = _wave_metadata(waves)
    config = Mock(); config.deploy_strategy = DeployStrategy.GRADUAL
    plan = Mock(); plan.strategy = UpdateStrategy.PRODUCTION; plan.multi_stage = False
    plan.helm_chart = "dummy-service"; plan.image_tag = "production-abc"

    with pytest.raises(RuntimeError, match="wave"):
        _group_changes_for_prs([_stack_change(s) for s in waves], plan, config, io)


def test_wave_grouping_missing_metadata_uses_defaults():
    """read_yaml returns None for dev stack → defaults to wave 0; others explicit."""
    waves_explicit = {
        "com-keboola-azure-north-europe": 1,
        "kbc-us-east-1": 2,
        "cloud-keboola-cs": 3,
    }
    dev_stack = "dev-keboola-gcp-us-central1"

    def _read(path):
        # Return None for the dev stack, metadata dict for the others
        for stack, wave in waves_explicit.items():
            if path == f"{stack}/stack-metadata.yaml":
                return {"rollout_wave": wave}
        return None  # covers the dev stack and any unknown path

    io = Mock()
    io.read_yaml.side_effect = _read
    config = Mock(); config.deploy_strategy = DeployStrategy.GRADUAL
    plan = Mock(); plan.strategy = UpdateStrategy.PRODUCTION; plan.multi_stage = False
    plan.helm_chart = "dummy-service"; plan.image_tag = "production-abc123"

    all_stacks = [dev_stack] + list(waves_explicit.keys())
    groups = _group_changes_for_prs([_stack_change(s) for s in all_stacks], plan, config, io)

    assert len(groups) == 4
    by_wave = {g["wave_number"]: g for g in groups}
    assert set(by_wave) == {0, 1, 2, 3}
    # The dev stack must have landed in wave 0
    assert dev_stack in by_wave[0]["stacks"]


from helm_image_updater.plan_builder import _create_pr_plan


def test_create_pr_plan_wave_sets_labels_and_branch_title():
    config = Mock(); config.automerge = False
    config.deploy_strategy = DeployStrategy.GRADUAL
    plan = Mock()
    plan.strategy = UpdateStrategy.PRODUCTION
    plan.multi_stage = False
    plan.helm_chart = "dummy-service"
    plan.image_tag = "production-abc123"
    plan.extra_tags = []
    plan.metadata = {}

    fc = Mock(); fc.file_path = "kbc-us-east-1/dummy-service/tag.yaml"
    group = {
        'stacks': ["kbc-us-east-1"],
        'changes': [{"stack": "kbc-us-east-1", "file_change": fc, "changes": []}],
        'base_branch': 'main',
        'pr_type': 'wave',
        'wave_number': 2,
        'labels': ["release:wave:2", "deploy:gradual"],
    }

    pr_plan = _create_pr_plan(group, plan, config)

    assert pr_plan.labels == group['labels']
    assert pr_plan.auto_merge is False
    assert "wave2" in pr_plan.branch_name
    assert "wave 2" in pr_plan.pr_title
    assert pr_plan.wave_number == 2


def test_wave_never_auto_merges_even_for_canary_strategy():
    plan = Mock(); plan.strategy = UpdateStrategy.CANARY
    assert _should_auto_merge(plan, "wave", user_requested=True) is False


from helm_image_updater.io_layer import IOLayer
from github.GithubException import GithubException


def test_create_pull_request_provisions_and_applies_labels():
    repo = Mock()
    # get_label raises 404 for the dynamic release:wave: label, succeeds otherwise
    def _get_label(name):
        if name.startswith("release:wave:"):
            raise GithubException(404, {"message": "Not Found"}, None)
        return Mock()
    repo.get_label.side_effect = _get_label
    pr = Mock(); pr.html_url = "http://x/1"; pr.number = 1
    repo.create_pull.return_value = pr

    io = IOLayer(Mock(), repo, dry_run=False, approve_github_repo=Mock())
    io.push_branch = Mock()  # avoid real git push

    io.create_pull_request(
        title="t", body="b", branch_name="br", base_branch="main",
        auto_merge=False,
        labels=["release:wave:2", "deploy:gradual"],
    )

    repo.create_label.assert_called()  # created the missing release:wave: label
    pr.add_to_labels.assert_called_once_with("release:wave:2", "deploy:gradual")


import pytest
from helm_image_updater.plan_builder import _guard_release_not_already_open
from helm_image_updater.manifest import manifest_block, build_manifest


def test_guard_raises_when_instance_id_already_open():
    io = Mock()
    body = manifest_block(build_manifest(app="connection", instance_id="connection-abc",
                                         display_name="c", waves={0: 10, 1: 11, 2: 12, 3: 13}))
    io.find_open_release_anchors.return_value = [(10, body)]
    with pytest.raises(RuntimeError, match="already has an open anchor"):
        _guard_release_not_already_open("connection-abc", io)


def test_guard_passes_when_no_matching_open_release():
    io = Mock()
    io.find_open_release_anchors.return_value = []
    _guard_release_not_already_open("connection-abc", io)  # no raise


def test_wave_grouping_excludes_e2e_stacks():
    waves = {
        "dev-keboola-gcp-us-central1": 0,
        "com-keboola-azure-north-europe": 1,
        "kbc-us-east-1": 2,
        "cloud-keboola-cs": 3,
    }
    io = Mock(); io.read_yaml.side_effect = _wave_metadata(waves)
    config = Mock(); config.deploy_strategy = DeployStrategy.GRADUAL
    plan = Mock(); plan.strategy = UpdateStrategy.PRODUCTION; plan.multi_stage = False
    plan.helm_chart = "dummy-service"; plan.image_tag = "production-abc"
    # an unlisted e2e stack must be dropped from waves, not placed in wave 3
    changes = [_stack_change(s) for s in waves] + [_stack_change("foo-bar-e2e")]
    groups = _group_changes_for_prs(changes, plan, config, io)
    all_stacks = [s for g in groups for s in g["stacks"]]
    assert "foo-bar-e2e" not in all_stacks
    assert len(groups) == 4
