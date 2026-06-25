"""Plan executor - executes a prepared plan."""

import re
from typing import Dict, List, Optional
from .models import UpdatePlan, ExecutionResult
from .io_layer import IOLayer
from .manifest import build_manifest, build_manual_manifest, manifest_block
from .exceptions import AutoApproveError

_PR_NUM_RE = re.compile(r"/pull/(\d+)")


def _pr_number_from_url(url: str) -> Optional[int]:
    m = _PR_NUM_RE.search(url or "")
    return int(m.group(1)) if m else None


def execute_plan(plan: UpdatePlan, io_layer: IOLayer) -> ExecutionResult:
    """
    Execute a prepared plan.
    
    This function performs all the I/O operations needed to implement
    the changes described in the plan.
    """
    result = ExecutionResult(success=True, dry_run=plan.dry_run)
    
    if not plan.has_changes():
        print("No changes to execute.")
        return result
    
    try:
        # Execute file changes
        _execute_file_changes(plan, io_layer, result)
        
        # Execute PR plans
        _execute_pr_plans(plan, io_layer, result)
        
    except Exception as e:
        result.success = False
        result.errors.append(f"Execution failed: {str(e)}")
    
    return result


def _execute_file_changes(plan: UpdatePlan, io_layer: IOLayer, result: ExecutionResult):
    """Write all file changes."""
    files_by_pr = _group_files_by_pr(plan)
    
    for pr_plan in plan.pr_plans:
        pr_files = files_by_pr.get(pr_plan.branch_name, {})
        
        for file_path, file_change in pr_files.items():
            if plan.dry_run:
                print(f"[DRY RUN] Would write to {file_path}")
                result.changes_made.append(file_change.change_description)
            else:
                # In real execution, files are written as part of PR creation
                print(file_change.change_description)
                result.files_written.append(file_path)
                result.changes_made.append(file_change.change_description)


def _group_files_by_pr(plan: UpdatePlan) -> Dict[str, Dict[str, any]]:
    """Group file changes by PR branch."""
    files_by_pr = {}
    
    for pr_plan in plan.pr_plans:
        files_by_pr[pr_plan.branch_name] = {}
        for file_path in pr_plan.files_to_commit:
            # Find the corresponding file change
            for file_change in plan.file_changes:
                if file_change.file_path == file_path:
                    files_by_pr[pr_plan.branch_name][file_path] = file_change
                    break
    
    return files_by_pr


def _execute_pr_plans(plan: UpdatePlan, io_layer: IOLayer, result: ExecutionResult):
    """Create all pull requests; then patch the anchor manifest — the wave-0 PR (wave mode)
    or the lowest-numbered member PR (manual-per-stack, ST-4157)."""
    wave_pr_numbers: Dict[int, int] = {}   # wave -> PR number
    wave0_body: Optional[str] = None       # the wave-0 PR's created body
    manual_pr_numbers: List[int] = []      # manual-per-stack member PR numbers
    manual_bodies: Dict[int, str] = {}     # member PR number -> its created body

    for pr_plan in plan.pr_plans:
        if plan.dry_run:
            print(f"[DRY RUN] Would create PR: {pr_plan.pr_title}")
            print(f"  Branch: {pr_plan.branch_name}")
            print(f"  Base: {pr_plan.base_branch}")
            print(f"  Auto-merge: {pr_plan.auto_merge}")
            print(f"  Files: {', '.join(pr_plan.files_to_commit)}")
            if pr_plan.labels:
                print(f"  Labels: {', '.join(pr_plan.labels)}")
            continue

        # A "member" PR is promoter-managed (a wave PR or a manual-per-stack member): it is
        # created unmerged + labelled, and a creation failure must withhold the manifest
        # rather than abort via the historical catch-all (which is for legacy single PRs).
        is_member = pr_plan.wave_number is not None or pr_plan.manual_member
        member_desc = (
            f"Wave {pr_plan.wave_number}" if pr_plan.wave_number is not None
            else f"Manual member '{pr_plan.branch_name}'"
        )

        relevant_file_changes = [fc for fc in plan.file_changes
                                 if fc.file_path in pr_plan.files_to_commit]
        io_layer.write_file_changes(relevant_file_changes)

        try:
            pr_url = io_layer.create_branch_commit_and_pr(
                branch_name=pr_plan.branch_name,
                files_to_commit=pr_plan.files_to_commit,
                commit_message=pr_plan.commit_message,
                pr_title=pr_plan.pr_title,
                pr_body=pr_plan.pr_body,
                base_branch=pr_plan.base_branch,
                auto_merge=pr_plan.auto_merge,
                labels=pr_plan.labels,
            )
        except AutoApproveError as exc:
            if not is_member or not exc.pr_url:
                raise  # non-member keeps historical behavior; no pr_url -> creation failure
            # The PR EXISTS (creation succeeded; only the post-create CODEOWNERS
            # auto-approval failed). Keep fanning out and still emit the manifest —
            # an unapproved member PR just waits for a human approval. Treating this as a
            # creation failure would orphan a labelled, manifest-less anchor the rerun
            # guard cannot see.
            result.success = False
            result.errors.append(
                f"{member_desc} PR created but auto-approve FAILED: {exc}. "
                f"Approve {exc.pr_url} manually; the release manifest is still emitted.")
            pr_url = exc.pr_url
        except Exception as exc:
            if not is_member:
                raise  # non-member plans keep the historical abort-via-catch-all behavior
            # A failed member PR already makes the release unusable (F3 withholds the
            # manifest), so creating further member PRs would only add orphans to clean
            # up. Record an actionable error and stop fanning out; the fall-through to the
            # patch step reports the collected-vs-missing picture.
            result.success = False
            result.errors.append(f"Failed to create {member_desc.lower()} PR ('{pr_plan.pr_title}'): {exc}")
            break

        if pr_url:
            result.pr_urls.append(pr_url)
            print(f"Created PR: {pr_plan.pr_title}")
            if pr_plan.wave_number is not None:
                num = _pr_number_from_url(pr_url)
                if num is not None:
                    wave_pr_numbers[pr_plan.wave_number] = num
                    if pr_plan.wave_number == 0:
                        wave0_body = pr_plan.pr_body
                else:
                    result.success = False
                    result.errors.append(
                        f"Could not parse PR number from URL '{pr_url}' for wave "
                        f"{pr_plan.wave_number}; manifest will be withheld (F3).")
            elif pr_plan.manual_member:
                num = _pr_number_from_url(pr_url)
                if num is not None:
                    manual_pr_numbers.append(num)
                    manual_bodies[num] = pr_plan.pr_body
                else:
                    result.success = False
                    result.errors.append(
                        f"Could not parse PR number from URL '{pr_url}' for manual member; "
                        f"manifest will be withheld (F3).")
        else:
            result.errors.append(f"Failed to create PR: {pr_plan.pr_title}")
            if is_member:
                result.success = False  # a missing member PR → withhold the manifest (F3)

    if not plan.dry_run and plan.manifest_context and wave_pr_numbers:
        _patch_anchor_manifest(plan, io_layer, wave_pr_numbers, wave0_body, result)
    if not plan.dry_run and plan.manifest_context and manual_pr_numbers:
        _patch_manual_anchor(plan, io_layer, manual_pr_numbers, manual_bodies, result)


def _wave_links_md(wave_pr_numbers: Dict[int, int]) -> str:
    """Render a clickable wave -> PR list for the anchor body (#N auto-links on GitHub)."""
    lines = ["### Release waves"]
    for wave in sorted(wave_pr_numbers):
        suffix = " (anchor — this PR)" if wave == 0 else ""
        lines.append(f"- wave {wave}: #{wave_pr_numbers[wave]}{suffix}")
    return "\n".join(lines)


def _patch_anchor_manifest(plan: UpdatePlan, io_layer: IOLayer, wave_pr_numbers: Dict[int, int],
                            wave0_body: Optional[str], result: ExecutionResult) -> None:
    """Build the v1 manifest from the collected {wave -> PR#} and patch the wave-0 body.

    F3: refuse to emit a PARTIAL manifest. Every declared wave PR must have a parsed PR
    number, or we withhold the manifest entirely (a manifest with a subset of waves is
    structurally valid to the promoter — it would treat it as a shorter release and orphan
    the un-listed wave PRs)."""
    ctx = plan.manifest_context
    expected = {p.wave_number for p in plan.pr_plans if p.wave_number is not None}
    if set(wave_pr_numbers) != expected or wave0_body is None:
        missing = sorted(expected - set(wave_pr_numbers))
        result.success = False
        result.errors.append(
            f"Manifest NOT written: missing PR numbers for waves {missing} "
            f"(collected {sorted(wave_pr_numbers)}) or wave-0 body unavailable; refusing to "
            f"emit a partial manifest that would orphan wave PRs (F3)."
        )
        # The release is incomplete and its manifest is withheld — so the already-created
        # wave PRs are dead. Close them (single point covering every partial-fan-out path)
        # so no orphaned, MANIFEST-LESS release:wave:0 anchor is left behind: the rerun guard
        # detects duplicates by parsing the instanceId from an anchor body, which such an
        # anchor lacks, so an orphan would let a duplicate fan-out through next run. Wave PRs
        # are unmerged (auto_merge=False), so closing them deploys nothing. (Halama review.)
        for w in sorted(wave_pr_numbers):
            num = wave_pr_numbers[w]
            try:
                io_layer.close_pr(num)
                print(f"Closed orphaned wave {w} PR #{num} (incomplete release; manifest withheld).")
            except Exception as exc:
                result.errors.append(
                    f"Could not close orphaned wave {w} PR #{num}: {exc}. Close it manually."
                )
        return
    anchor = wave_pr_numbers[0]
    manifest = build_manifest(
        app=ctx["app"], instance_id=ctx["instance_id"], display_name=ctx["display_name"],
        waves=wave_pr_numbers, source_sha=ctx.get("source_sha"), source_pr=ctx.get("source_pr"),
    )
    links_md = _wave_links_md(wave_pr_numbers)
    new_body = f"{wave0_body}\n\n{links_md}\n\n{manifest_block(manifest)}"
    try:
        io_layer.update_pull_request_body(anchor, new_body)
    except Exception as exc:
        result.success = False
        result.errors.append(
            f"Manifest patch FAILED on wave-0 anchor PR #{anchor}: {exc}. "
            f"The release is manifest-less; close wave PRs "
            f"{sorted(wave_pr_numbers.values())} before re-running, or patch the anchor "
            f"body manually."
        )
        return
    print(f"Release manifest written to wave-0 anchor PR #{anchor} "
          f"(instanceId={ctx['instance_id']}, waves={manifest['waves']})")


def _manual_members_md(members: List[int], anchor: int) -> str:
    """Render a clickable member-PR list for the manual anchor body (#N auto-links)."""
    lines = ["### Release members (manual-per-stack)"]
    for n in sorted(members):
        suffix = " (anchor — this PR)" if n == anchor else ""
        lines.append(f"- #{n}{suffix}")
    return "\n".join(lines)


def _patch_manual_anchor(plan: UpdatePlan, io_layer: IOLayer, manual_pr_numbers: List[int],
                         manual_bodies: Dict[int, str], result: ExecutionResult) -> None:
    """manual-per-stack (ST-4157): the anchor is the LOWEST-numbered member PR. Stamp it with
    `release:anchor` (discovery), then patch the flat-member manifest into its body.

    F3: refuse a PARTIAL manifest. Every declared manual member PRPlan must have a parsed PR
    number, else withhold the manifest entirely and close the created members (a manifest-less
    `release:anchor` would otherwise leak past the rerun guard, exactly like a manifest-less
    wave-0 anchor).

    Ordering mirrors the wave create-then-patch: `release:anchor` is applied BEFORE the body
    patch so a body-patch failure leaves a DISCOVERABLE (manifest-less → grace-skipped, then
    Conflicted) anchor rather than a silent, undiscovered release."""
    ctx = plan.manifest_context
    expected = sum(1 for p in plan.pr_plans if p.manual_member)
    anchor = min(manual_pr_numbers)
    anchor_body = manual_bodies.get(anchor)
    if len(manual_pr_numbers) != expected or anchor_body is None:
        result.success = False
        result.errors.append(
            f"Manifest NOT written: collected {sorted(manual_pr_numbers)} of {expected} manual "
            f"member PRs (or anchor body unavailable); refusing to emit a partial manifest that "
            f"would orphan member PRs (F3)."
        )
        for n in sorted(manual_pr_numbers):
            try:
                io_layer.close_pr(n)
                print(f"Closed orphaned manual member PR #{n} (incomplete release; manifest withheld).")
            except Exception as exc:
                result.errors.append(
                    f"Could not close orphaned manual member PR #{n}: {exc}. Close it manually."
                )
        return

    members = sorted(manual_pr_numbers)
    # release:anchor FIRST (discovery), so a later body-patch failure stays visible.
    try:
        io_layer.add_label(anchor, "release:anchor")
    except Exception as exc:
        # The label-add is the FIRST mutation: nothing discoverable exists yet (no
        # release:anchor, no manifest). Leaving the members open would strand an
        # undiscoverable set a rerun could duplicate — so close them (mirror the F3
        # cleanup). They are unmerged, so closing deploys nothing. (Codex review.)
        result.success = False
        result.errors.append(
            f"Failed to apply 'release:anchor' to manual anchor PR #{anchor}: {exc}. "
            f"Closing the created member PRs {members} (manifest withheld); re-run to retry."
        )
        for n in members:
            try:
                io_layer.close_pr(n)
                print(f"Closed manual member PR #{n} (release:anchor failed; manifest withheld).")
            except Exception as close_exc:
                result.errors.append(
                    f"Could not close manual member PR #{n}: {close_exc}. Close it manually."
                )
        return

    manifest = build_manual_manifest(
        app=ctx["app"], instance_id=ctx["instance_id"], display_name=ctx["display_name"],
        members=members, source_sha=ctx.get("source_sha"), source_pr=ctx.get("source_pr"),
    )
    links_md = _manual_members_md(members, anchor)
    new_body = f"{anchor_body}\n\n{links_md}\n\n{manifest_block(manifest)}"
    try:
        io_layer.update_pull_request_body(anchor, new_body)
    except Exception as exc:
        result.success = False
        result.errors.append(
            f"Manifest patch FAILED on manual anchor PR #{anchor}: {exc}. The release is "
            f"manifest-less; close member PRs {members} before re-running, or patch the anchor "
            f"body manually."
        )
        return
    print(f"Release manifest written to manual anchor PR #{anchor} "
          f"(instanceId={ctx['instance_id']}, members={members}); release:anchor applied.")
