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
    # ST-4035: clickable wave list sits between the original body and the manifest,
    # so the FIRST ```json fence (asserted above) still parses to the manifest.
    assert "### Release waves" in new_body
    assert "- wave 0: #10 (anchor — this PR)" in new_body
    assert "- wave 1: #11" in new_body
    assert "- wave 2: #12" in new_body
    assert "- wave 3: #13" in new_body


def _standard_2wave_plan():
    """A promoter-managed standard release: wave 0 = dev anchor, wave 1 = prod."""
    plan = UpdatePlan(strategy=UpdateStrategy.PRODUCTION, helm_chart="connection",
                      image_tag="production-abc")
    plan.manifest_context = {"app": "connection", "instance_id": "connection-deadbeef0123",
                             "display_name": "connection@production-abc",
                             "source_sha": "deadbeef0123", "source_pr": None}
    for w in range(2):
        fc = FileChange(file_path=f"stack{w}/connection/tag.yaml", old_content="a",
                        new_content="b", change_description="d")
        plan.file_changes.append(fc)
        plan.pr_plans.append(PRPlan(branch_name=f"connection-wave{w}-production-abc-xxxx",
                                    pr_title=f"w{w}", pr_body=f"BODY{w}", base_branch="main",
                                    auto_merge=False, files_to_commit=[fc.file_path],
                                    commit_message="c",
                                    labels=[f"release:wave:{w}", "deploy:standard"],
                                    wave_number=w))
    return plan


def test_executor_standard_2wave_manifest_shape():
    """ST-4126: a 2-wave standard release emits a v1 manifest with waves={'0':N,'1':M}
    on the wave-0 (dev) anchor; deploy:standard labels; instanceId=<app>-<sha12>."""
    plan = _standard_2wave_plan()
    io = MagicMock()
    io.create_branch_commit_and_pr.side_effect = [
        "https://github.com/keboola/kbc-stacks/pull/20",  # wave 0 (dev anchor)
        "https://github.com/keboola/kbc-stacks/pull/21",  # wave 1 (prod)
    ]
    result = execute_plan(plan, io)

    io.update_pull_request_body.assert_called_once()
    (anchor_num, new_body), _ = io.update_pull_request_body.call_args
    assert anchor_num == 20  # dev anchor
    manifest = json.loads(JSON_FENCE_RE.findall(new_body)[0])
    assert manifest["manifestVersion"] == "v1"
    assert manifest["instanceId"] == "connection-deadbeef0123"
    assert manifest["app"] == "connection"
    assert manifest["anchorWave"] == 0
    assert manifest["waves"] == {"0": 20, "1": 21}
    assert new_body.startswith("BODY0")


def test_executor_standard_1wave_manifest_shape():
    """1-wave fallback (no dev or no prod): a single wave-0 release emits waves={'0':N}."""
    plan = UpdatePlan(strategy=UpdateStrategy.PRODUCTION, helm_chart="connection",
                      image_tag="production-abc")
    plan.manifest_context = {"app": "connection", "instance_id": "connection-cafef00d0001",
                             "display_name": "connection@production-abc",
                             "source_sha": "cafef00d0001", "source_pr": None}
    fc = FileChange(file_path="prod/connection/tag.yaml", old_content="a", new_content="b",
                    change_description="d")
    plan.file_changes.append(fc)
    plan.pr_plans.append(PRPlan(branch_name="connection-wave0-production-abc-xxxx",
                                pr_title="w0", pr_body="BODY0", base_branch="main",
                                auto_merge=False, files_to_commit=[fc.file_path],
                                commit_message="c",
                                labels=["release:wave:0", "deploy:standard"], wave_number=0))
    io = MagicMock()
    io.create_branch_commit_and_pr.return_value = "https://github.com/keboola/kbc-stacks/pull/30"
    execute_plan(plan, io)

    io.update_pull_request_body.assert_called_once()
    (anchor_num, new_body), _ = io.update_pull_request_body.call_args
    assert anchor_num == 30
    manifest = json.loads(JSON_FENCE_RE.findall(new_body)[0])
    assert manifest["waves"] == {"0": 30}
    assert manifest["anchorWave"] == 0


def test_executor_wave_manifest_includes_image_tag_and_extra_tags():
    """ST-4277 B3: the wave-0 build_manifest call site must forward manifest_context's
    image_tag/extra_tags so they reach the live anchor PR body (imageTag/extraTags)."""
    plan = _wave_plan()
    plan.manifest_context["image_tag"] = "production-abc"
    plan.manifest_context["extra_tags"] = [{"path": "foo.bar", "value": "baz"}]
    io = MagicMock()
    io.create_branch_commit_and_pr.side_effect = [
        f"https://github.com/keboola/kbc-stacks/pull/{10 + w}" for w in range(4)
    ]
    execute_plan(plan, io)

    io.update_pull_request_body.assert_called_once()
    (_, new_body), _ = io.update_pull_request_body.call_args
    manifest = json.loads(JSON_FENCE_RE.findall(new_body)[0])
    assert manifest["imageTag"] == "production-abc"
    assert manifest["extraTags"] == ["foo.bar=baz"]


def _manual_manifest_plan():
    """A manual-per-stack style plan (2 member PRs, no waves)."""
    plan = UpdatePlan(strategy=UpdateStrategy.PRODUCTION, helm_chart="connection",
                      image_tag="production-abc")
    plan.manifest_context = {"app": "connection", "instance_id": "connection-manual-abc",
                             "display_name": "connection@production-abc",
                             "source_sha": "abc", "source_pr": None,
                             "image_tag": "production-abc",
                             "extra_tags": [{"path": "foo.bar", "value": "baz"}]}
    for i, stack in enumerate(["s0", "s1"]):
        fc = FileChange(file_path=f"{stack}/connection/tag.yaml", old_content="a",
                        new_content="b", change_description="d")
        plan.file_changes.append(fc)
        plan.pr_plans.append(PRPlan(branch_name=f"connection-manual-{stack}-production-abc-xxxx",
                                    pr_title=f"m{i}", pr_body=f"BODY{i}", base_branch="main",
                                    auto_merge=False, files_to_commit=[fc.file_path],
                                    commit_message="c", labels=["deploy:manual-per-stack"],
                                    manual_member=True))
    return plan


def test_executor_manual_manifest_includes_image_tag_and_extra_tags():
    """ST-4277 B3: the manual-per-stack build_manual_manifest call site must ALSO forward
    manifest_context's image_tag/extra_tags -- both call sites, or the fields never reach
    a live PR body depending on which grouping mode produced the release."""
    plan = _manual_manifest_plan()
    io = MagicMock()
    io.create_branch_commit_and_pr.side_effect = [
        "https://github.com/keboola/kbc-stacks/pull/30",
        "https://github.com/keboola/kbc-stacks/pull/31",
    ]
    execute_plan(plan, io)

    io.update_pull_request_body.assert_called_once()
    (_, new_body), _ = io.update_pull_request_body.call_args
    manifest = json.loads(JSON_FENCE_RE.findall(new_body)[0])
    assert manifest["imageTag"] == "production-abc"
    assert manifest["extraTags"] == ["foo.bar=baz"]


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


def test_executor_wave_creation_exception_caught_breaks_and_reports():
    """A raising wave-PR creation (the realistic live failure: git push / GitHub 5xx) must
    NOT bubble to execute_plan's generic catch-all: the executor records an actionable
    per-wave error, stops fanning out (no further creates -> no extra orphans), withholds
    the manifest (F3), and fails the run."""
    plan = _wave_plan()
    io = MagicMock()
    io.create_branch_commit_and_pr.side_effect = [
        "https://github.com/keboola/kbc-stacks/pull/10",
        "https://github.com/keboola/kbc-stacks/pull/11",
        Exception("boom-502"),  # wave 2 creation raises
        "https://github.com/keboola/kbc-stacks/pull/13",  # must never be attempted
    ]
    result = execute_plan(plan, io)

    assert io.create_branch_commit_and_pr.call_count == 3  # broke after the failure
    io.update_pull_request_body.assert_not_called()        # manifest withheld (F3)
    assert result.success is False
    assert any("wave 2" in e and "boom-502" in e for e in result.errors)
    # The F3 reporter still emits the collected-vs-missing picture.
    assert any("waves [2, 3]" in e and "[0, 1]" in e for e in result.errors)


def test_executor_closes_orphan_wave_prs_when_manifest_withheld():
    """A partial fan-out (a wave PR fails to create) withholds the manifest (F3) AND closes
    the already-created lower-wave PRs, so no orphaned manifest-less release:wave:0 anchor is
    left behind — HIU's rerun guard detects duplicates by parsing the instanceId from an
    anchor body, which a manifest-less (withheld) anchor lacks, so an orphan would otherwise
    let a duplicate fan-out through on the next run (Halama review of #37)."""
    plan = _wave_plan()
    io = MagicMock()
    io.create_branch_commit_and_pr.side_effect = [
        "https://github.com/keboola/kbc-stacks/pull/10",  # wave 0 (the anchor)
        "https://github.com/keboola/kbc-stacks/pull/11",  # wave 1
        Exception("boom-502"),                            # wave 2 creation raises
    ]
    result = execute_plan(plan, io)

    assert result.success is False
    io.update_pull_request_body.assert_not_called()        # manifest withheld (F3)
    # the already-created lower-wave PRs are closed → no orphan anchor for the guard to miss
    closed = {c.args[0] for c in io.close_pr.call_args_list}
    assert closed == {10, 11}


def test_executor_wave_auto_approve_failure_keeps_fanout_and_manifest():
    """AutoApproveError means the PR EXISTS (creation succeeded, only the CODEOWNERS
    approval failed) — the executor must keep fanning out and still emit the manifest
    (an unapproved wave PR just waits for a human approval), instead of orphaning a
    labelled, manifest-less anchor the rerun guard cannot see. Run still fails loudly."""
    from helm_image_updater.exceptions import AutoApproveError
    plan = _wave_plan()
    io = MagicMock()
    io.create_branch_commit_and_pr.side_effect = [
        "https://github.com/keboola/kbc-stacks/pull/10",
        "https://github.com/keboola/kbc-stacks/pull/11",
        AutoApproveError("approval boom", pr_url="https://github.com/keboola/kbc-stacks/pull/12"),
        "https://github.com/keboola/kbc-stacks/pull/13",
    ]
    result = execute_plan(plan, io)

    assert io.create_branch_commit_and_pr.call_count == 4  # fan-out NOT aborted
    io.update_pull_request_body.assert_called_once()       # manifest still patched
    (anchor_num, new_body), _ = io.update_pull_request_body.call_args
    assert anchor_num == 10
    manifest = json.loads(JSON_FENCE_RE.findall(new_body)[0])
    assert manifest["waves"] == {"0": 10, "1": 11, "2": 12, "3": 13}  # incl. the unapproved PR
    assert result.success is False  # loud: operator must approve manually
    assert any("auto-approve FAILED" in e and "Wave 2" in e for e in result.errors)
    assert "https://github.com/keboola/kbc-stacks/pull/12" in result.pr_urls


def test_executor_non_wave_creation_exception_still_propagates_to_catch_all():
    """Non-wave plans keep the historical behavior: a raising creation aborts the run via
    execute_plan's catch-all (no per-PR continue/break semantics change)."""
    plan = UpdatePlan(strategy=UpdateStrategy.PRODUCTION, helm_chart="connection", image_tag="t")
    fc = FileChange(file_path="s/connection/tag.yaml", old_content="a", new_content="b",
                    change_description="d")
    plan.file_changes.append(fc)
    plan.pr_plans.append(PRPlan(branch_name="b", pr_title="t", pr_body="x", base_branch="main",
                                auto_merge=False, files_to_commit=[fc.file_path], commit_message="c"))
    io = MagicMock()
    io.create_branch_commit_and_pr.side_effect = Exception("boom")
    result = execute_plan(plan, io)
    assert result.success is False
    assert any("Execution failed" in e and "boom" in e for e in result.errors)
