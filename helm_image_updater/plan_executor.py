"""Plan executor - executes a prepared plan."""

import time
from typing import Dict, List
from .models import UpdatePlan, ExecutionResult
from .io_layer import IOLayer


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
    """Create all pull requests."""
    print(f"📋 Creating {len(plan.pr_plans)} PRs with 2-second delays to prevent race conditions...")
    
    for i, pr_plan in enumerate(plan.pr_plans):
        # Add delay between PRs to prevent GitHub API race conditions
        if i > 0:
            print(f"⏱️ Waiting 2 seconds before creating next PR to prevent race conditions...")
            time.sleep(2)
        if plan.dry_run:
            print(f"[DRY RUN] Would create PR: {pr_plan.pr_title}")
            print(f"  Branch: {pr_plan.branch_name}")
            print(f"  Base: {pr_plan.base_branch}")
            print(f"  Auto-merge: {pr_plan.auto_merge}")
            print(f"  Files: {', '.join(pr_plan.files_to_commit)}")
        else:
            # Step 1: Write files to disk first
            relevant_file_changes = [fc for fc in plan.file_changes 
                                   if fc.file_path in pr_plan.files_to_commit]
            io_layer.write_file_changes(relevant_file_changes)
            
            # Step 2: Create branch, commit, and PR (files already exist on disk)
            print(f"🔍 DEBUG: About to call create_branch_commit_and_pr with auto_merge={pr_plan.auto_merge}")
            print(f"🔍 DEBUG: PR title: {pr_plan.pr_title}")
            pr_url = io_layer.create_branch_commit_and_pr(
                branch_name=pr_plan.branch_name,
                files_to_commit=pr_plan.files_to_commit,  # List[str] - just paths
                commit_message=pr_plan.commit_message,
                pr_title=pr_plan.pr_title,
                pr_body=pr_plan.pr_body,
                base_branch=pr_plan.base_branch,
                auto_merge=pr_plan.auto_merge
            )
            
            if pr_url:
                result.pr_urls.append(pr_url)
                print(f"Created PR: {pr_plan.pr_title}")
                if pr_plan.auto_merge:
                    print(f"  Auto-merged: {pr_url}")
            else:
                result.errors.append(f"Failed to create PR: {pr_plan.pr_title}")