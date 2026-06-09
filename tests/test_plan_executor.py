# tests/test_plan_executor.py  (add)
import json, re
from unittest.mock import MagicMock
from helm_image_updater.models import UpdatePlan, PRPlan, FileChange, UpdateStrategy
from helm_image_updater.plan_executor import execute_plan

JSON_FENCE_RE = re.compile(r"```json\r?\n(.*?)```", re.DOTALL)


def _wave_plan():
    plan = UpdatePlan(strategy=UpdateStrategy.PRODUCTION, helm_chart="connection", image_tag="t")
    plan.manifest_context = {"app": "connection", "instance_id": "connection-abc",
                             "display_name": "connection@t", "source_sha": "abc", "source_pr": None}
    for w in range(4):
        fc = FileChange(file_path=f"stack{w}/connection/tag.yaml", old_content="a", new_content="b",
                        change_description="d")
        plan.file_changes.append(fc)
        plan.pr_plans.append(PRPlan(branch_name=f"connection-wave{w}-t-xxxx", pr_title=f"w{w}",
                                    pr_body=f"BODY{w}", base_branch="main", auto_merge=False,
                                    files_to_commit=[fc.file_path], commit_message="c",
                                    labels=[f"release:wave:{w}", "deploy:gradual"], wave_number=w))
    return plan


def test_executor_patches_wave0_anchor_with_manifest():
    plan = _wave_plan()
    io = MagicMock()
    # create_branch_commit_and_pr returns the PR URL; wave w -> PR number 10+w.
    io.create_branch_commit_and_pr.side_effect = [
        f"https://github.com/keboola/kbc-stacks/pull/{10 + w}" for w in range(4)
    ]
    execute_plan(plan, io)

    io.update_pull_request_body.assert_called_once()
    (anchor_num, new_body), _ = io.update_pull_request_body.call_args
    assert anchor_num == 10  # wave-0 PR number
    fences = JSON_FENCE_RE.findall(new_body)
    manifest = json.loads(fences[0])
    assert manifest["instanceId"] == "connection-abc"
    assert manifest["app"] == "connection"
    assert manifest["anchorWave"] == 0
    assert manifest["waves"] == {"0": 10, "1": 11, "2": 12, "3": 13}
    assert new_body.startswith("BODY0")  # appended to the original wave-0 body


def test_executor_no_patch_when_not_wave_mode():
    plan = UpdatePlan(strategy=UpdateStrategy.PRODUCTION, helm_chart="connection", image_tag="t")
    fc = FileChange(file_path="s/connection/tag.yaml", old_content="a", new_content="b", change_description="d")
    plan.file_changes.append(fc)
    plan.pr_plans.append(PRPlan(branch_name="b", pr_title="t", pr_body="x", base_branch="main",
                                auto_merge=False, files_to_commit=[fc.file_path], commit_message="c"))
    io = MagicMock(); io.create_branch_commit_and_pr.return_value = "https://github.com/o/r/pull/5"
    execute_plan(plan, io)
    io.update_pull_request_body.assert_not_called()


def test_executor_withholds_manifest_on_partial_creation():
    # F3: if any wave PR fails to create (returns None), the manifest must NOT be patched —
    # a partial manifest would orphan the un-listed wave PRs.
    plan = _wave_plan()
    io = MagicMock()
    io.create_branch_commit_and_pr.side_effect = [
        "https://github.com/keboola/kbc-stacks/pull/10",
        "https://github.com/keboola/kbc-stacks/pull/11",
        None,  # wave 2 creation failed
        "https://github.com/keboola/kbc-stacks/pull/13",
    ]
    result = execute_plan(plan, io)
    io.update_pull_request_body.assert_not_called()
    assert result.success is False


def test_executor_failure_on_unparseable_pr_url():
    """Wave 2 returns an URL without a PR number → no manifest patch, result.success False,
    and an error message that mentions wave 2."""
    plan = _wave_plan()
    io = MagicMock()
    io.create_branch_commit_and_pr.side_effect = [
        "https://github.com/o/r/pull/10",   # wave 0
        "https://github.com/o/r/pull/11",   # wave 1
        "https://github.com/o/r/no-number",  # wave 2 — unparseable
        "https://github.com/o/r/pull/13",   # wave 3
    ]
    result = execute_plan(plan, io)
    io.update_pull_request_body.assert_not_called()
    assert result.success is False
    assert any("for wave 2" in e for e in result.errors)
    assert any("waves [2]" in e for e in result.errors)


def test_executor_manifest_patch_failure_is_caught():
    """update_pull_request_body throws → result.success False, error contains
    'Manifest patch FAILED' and the wave PR numbers; no exception escapes execute_plan."""
    plan = _wave_plan()
    io = MagicMock()
    io.create_branch_commit_and_pr.side_effect = [
        f"https://github.com/o/r/pull/{10 + w}" for w in range(4)
    ]
    io.update_pull_request_body.side_effect = Exception("boom")
    result = execute_plan(plan, io)
    assert result.success is False
    assert any("Manifest patch FAILED" in e for e in result.errors)
    # All four wave PR numbers must appear in the error
    for w in range(4):
        assert any(str(10 + w) in e for e in result.errors)
