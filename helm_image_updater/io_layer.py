"""
I/O Layer for Helm Image Updater

This module contains all I/O operations (file system, Git, GitHub)
separated from business logic. This is the "imperative shell" that
handles all side effects.
"""

import os
import json
from pathlib import Path
from typing import List, Dict, Optional, Any, Tuple
import yaml
import dpath
from git import Repo
from github import Github
from github.GithubException import GithubException
from time import sleep

from .exceptions import AutoMergeError, AutoApproveError


class IOLayer:
    """Handles all I/O operations for the application."""
    
    def __init__(self, repo: Repo, github_repo: Any, dry_run: bool = False, *, approve_github_repo: Any, service: Optional[str] = None):
        """Initialize the I/O layer.

        Args:
            repo: Git repository object
            github_repo: GitHub repository object
            dry_run: If True, don't perform actual writes
            approve_github_repo: GitHub repository object authenticated as a CODEOWNERS team member, used to auto-approve PRs
            service: Name of the helm chart being updated this run. When set, every PR
                created via this layer is labelled `app:<service>` so PRs can be filtered
                by service. Constant for a run (HIU updates one chart per invocation).
        """
        self.repo = repo
        self.github_repo = github_repo
        self.dry_run = dry_run
        self.approve_github_repo = approve_github_repo
        self.service = service
    
    # -----------------------------------------------------------------------------
    # File System Operations
    # -----------------------------------------------------------------------------
    
    def read_file(self, path: str) -> Optional[str]:
        """Read a text file.
        
        Args:
            path: Path to the file
            
        Returns:
            File content as string or None if file doesn't exist
        """
        file_path = Path(path)
        if not file_path.exists():
            return None
        
        with file_path.open() as f:
            return f.read()
    
    def read_yaml(self, path: str) -> Optional[Dict[str, Any]]:
        """Read a YAML file and return its contents.
        
        Args:
            path: Path to the YAML file
            
        Returns:
            Dictionary with YAML contents or None if file doesn't exist
        """
        file_path = Path(path)
        if not file_path.exists():
            return None
        
        with file_path.open() as f:
            return yaml.safe_load(f)
    
    def write_yaml(self, path: str, data: Dict[str, Any]) -> bool:
        """Write data to a YAML file.
        
        Args:
            path: Path to the YAML file
            data: Data to write
            
        Returns:
            True if written, False if dry run
        """
        if self.dry_run:
            print(f"[DRY RUN] Would write to {path}")
            return False
        
        file_path = Path(path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        
        with file_path.open('w') as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False)

        return True
    
    def write_file_changes(self, file_changes) -> bool:
        """Write multiple file changes to disk.
        
        Args:
            file_changes: List of FileChange objects to write
            
        Returns:
            True if files were written, False if dry run
        """
        if self.dry_run:
            for file_change in file_changes:
                print(f"[DRY RUN] Would write to {file_change.file_path}")
            return False
        
        for file_change in file_changes:
            file_path = Path(file_change.file_path)
            file_path.parent.mkdir(parents=True, exist_ok=True)
            with file_path.open('w') as f:
                f.write(file_change.new_content)
        
        return True
    
    def read_shared_values_yaml(self, stack: str) -> Optional[Dict[str, Any]]:
        """Read and parse <stack>/shared-values.yaml.
        
        Args:
            stack: Stack name to read shared values for
            
        Returns:
            Dictionary with shared values contents or None if file doesn't exist
        """
        file_path = Path(stack) / "shared-values.yaml"
        if not file_path.exists():
            return None
        
        try:
            with file_path.open() as f:
                return yaml.safe_load(f)
        except yaml.YAMLError:
            return None
    
    # -----------------------------------------------------------------------------
    # Git Operations
    # -----------------------------------------------------------------------------
    
    def checkout_branch(self, branch_name: str, create: bool = False) -> bool:
        """Checkout a Git branch.
        
        Args:
            branch_name: Name of the branch
            create: If True, create a new branch
            
        Returns:
            True if successful, False if dry run
        """
        if self.dry_run:
            action = "create and checkout" if create else "checkout"
            print(f"[DRY RUN] Would {action} branch: {branch_name}")
            return False
        
        if create:
            self.repo.git.checkout('-b', branch_name)
        else:
            self.repo.git.checkout(branch_name)
        
        return True
    
    def switch_branch(self, branch_name: str) -> bool:
        """Switch to a different branch, pulling latest changes.
        
        Args:
            branch_name: Name of the branch to switch to
            
        Returns:
            True if successful
        """
        if self.dry_run:
            print(f"[DRY RUN] Would switch to branch: {branch_name}")
            return False
        
        # Checkout the branch
        self.repo.git.checkout(branch_name)
        # Pull latest changes
        self.repo.git.pull("origin", branch_name)
        
        return True
    
    def add_files(self, files: List[str]) -> bool:
        """Add files to Git staging area.
        
        Args:
            files: List of file paths to add
            
        Returns:
            True if successful, False if dry run
        """
        if self.dry_run:
            print(f"[DRY RUN] Would add files: {', '.join(files)}")
            return False
        
        for file in files:
            self.repo.git.add(file)
        
        return True
    
    def commit(self, message: str) -> bool:
        """Create a Git commit.
        
        Args:
            message: Commit message
            
        Returns:
            True if successful, False if dry run
        """
        if self.dry_run:
            print(f"[DRY RUN] Would commit with message: {message}")
            return False
        
        self.repo.git.commit('-m', message)
        return True
    
    def push_branch(self, branch_name: str, remote: str = "origin") -> bool:
        """Push a branch to remote.
        
        Args:
            branch_name: Name of the branch
            remote: Remote name (default: origin)
            
        Returns:
            True if successful, False if dry run
        """
        if self.dry_run:
            print(f"[DRY RUN] Would push {branch_name} to {remote}")
            return False
        
        self.repo.git.push(remote, branch_name)
        return True
    
    # -----------------------------------------------------------------------------
    # GitHub Operations
    # -----------------------------------------------------------------------------
    
    def create_pull_request(
        self,
        title: str,
        body: str,
        branch_name: str,
        base_branch: str = "main",
        auto_merge: bool = False,
        labels: Optional[List[str]] = None,
    ) -> Optional[str]:
        """Create a GitHub pull request.

        Args:
            title: PR title
            body: PR body/description
            branch_name: Head branch name
            base_branch: Base branch name
            auto_merge: If True, attempt to auto-merge
            labels: Optional labels to provision (create-if-missing) and apply

        Returns:
            PR URL if created, None if dry run
        """
        print(f"🚀 Creating PR: '{title}'")
        print(f"   - Base: {base_branch}, Head: {branch_name}")
        print(f"   - Auto-merge requested: {'YES' if auto_merge else 'NO'}")

        # Universal `app:<service>` label: every PR HIU creates is tagged with the
        # chart being updated so PRs can be filtered by service (ST-4128). Injected at
        # this chokepoint (not per-caller) so no PR-creation path can miss it.
        if self.service:
            app_label = f"app:{self.service}"
            labels = list(labels) if labels else []
            if app_label not in labels:
                labels.append(app_label)

        if self.dry_run:
            merge_status = "and auto-merge" if auto_merge else "without auto-merge"
            print(f"[DRY RUN] Would create PR: '{title}' {merge_status}")
            print(f"[DRY RUN] Base: {base_branch}, Head: {branch_name}")
            if labels:
                print(f"[DRY RUN] Labels: {', '.join(labels)}")
            print(f"[DRY RUN] Body:\n{body}")
            return None

        # Push the branch first
        self.push_branch(branch_name)

        # Create the PR
        pr = self.github_repo.create_pull(
            title=title,
            body=body,
            head=branch_name,
            base=base_branch
        )

        print(f"PR created: {pr.html_url}")

        if labels:
            self._ensure_labels_exist(labels)
            pr.add_to_labels(*labels)
            print(f"🏷️  Applied labels: {', '.join(labels)}")

        # Auto-merge if requested
        if auto_merge:
            print(f"🔄 Auto-merge requested - attempting to merge PR...")
            self._attempt_auto_merge(pr)
        else:
            print(f"⏸️ Auto-merge NOT requested - PR left for manual review")
            self._auto_approve_pr(pr)

        return pr.html_url
    
    def _attempt_auto_merge(self, pr, max_retries: int = 10, retry_delay: int = 5):
        """Attempt to auto-merge a PR with retries.

        Args:
            pr: GitHub PR object
            max_retries: Maximum number of retry attempts
            retry_delay: Delay between retries in seconds

        Raises:
            AutoMergeError: If PR cannot be merged after max_retries or has conflicts
            GithubException: For other GitHub API errors
        """
        for attempt in range(max_retries):
            try:
                pr.update()  # Refresh PR data

                if pr.mergeable is None:
                    print(f"PR mergeability not yet determined, waiting... (attempt {attempt + 1}/{max_retries})")
                    if attempt < max_retries - 1:
                        sleep(retry_delay)
                        continue
                    else:
                        # Exhausted retries with mergeable still None
                        error_msg = f"Failed to auto-merge PR after {max_retries} attempts: PR mergeability could not be determined. PR: {pr.html_url}"
                        raise AutoMergeError(error_msg, pr_url=pr.html_url)
                elif not pr.mergeable:
                    error_msg = f"PR is not mergeable due to conflicts: {pr.html_url}"
                    print(error_msg)
                    raise AutoMergeError(error_msg, pr_url=pr.html_url)

                pr.merge()
                print(f"PR automatically merged: {pr.html_url}")
                return  # Successfully merged

            except AutoMergeError:
                # Re-raise AutoMergeError without modification
                raise
            except GithubException as e:
                if e.status == 405:
                    if attempt < max_retries - 1:
                        error_message = str(e.data.get("message", ""))
                        print(f"PR not ready to merge ({error_message}), waiting... (attempt {attempt + 1}/{max_retries})")
                        sleep(retry_delay)
                    else:
                        error_msg = f"Failed to merge PR after {max_retries} attempts: {pr.html_url}"
                        print(error_msg)
                        raise AutoMergeError(error_msg, pr_url=pr.html_url)
                else:
                    raise
    
    def _auto_approve_pr(self, pr, max_retries: int = 5, retry_delay: int = 2):
        """Auto-approve a PR using a CODEOWNERS team member's token.

        This satisfies CODEOWNERS approval requirements so that humans
        can merge PRs without waiting for additional reviews.

        Args:
            pr: GitHub PR object
            max_retries: Maximum number of retry attempts for transient errors
            retry_delay: Base delay in seconds between retries (exponential backoff)

        Raises:
            AutoApproveError: If the PR cannot be auto-approved after retries or due to GitHub API errors.
        """
        for attempt in range(max_retries):
            try:
                approve_pr = self.approve_github_repo.get_pull(pr.number)
                approve_pr.create_review(event="APPROVE")
                print(f"✅ PR auto-approved: {pr.html_url}")
                return
            except GithubException as e:
                if e.status == 404 and attempt < max_retries - 1:
                    print(f"PR not yet available for approval (404), retrying... (attempt {attempt + 1}/{max_retries})")
                    sleep(retry_delay * (2 ** attempt))
                else:
                    raise AutoApproveError(
                        f"Failed to auto-approve PR after {attempt + 1} attempt(s): {e}",
                        pr_url=pr.html_url
                    )

    def find_prs_by_label(self, label: str) -> List[int]:
        """Return open PR numbers carrying the given label (idempotency check)."""
        numbers = []
        for issue in self.github_repo.get_issues(state="open", labels=[label]):
            if issue.pull_request is not None:
                numbers.append(issue.number)
        return numbers

    def update_pull_request_body(self, pr_number: int, body: str) -> None:
        """Patch a PR's body in place (used to inject the release manifest into the
        wave-0 anchor once all wave PR numbers are known)."""
        if self.dry_run:
            print(f"[DRY RUN] Would update body of PR #{pr_number}")
            return
        pr = self.github_repo.get_pull(pr_number)
        pr.edit(body=body)
        print(f"📝 Updated PR #{pr_number} body (release manifest injected)")

    def add_label(self, pr_number: int, label: str) -> None:
        """Add a single label to an existing PR (used to mark the manual-per-stack anchor
        with `release:anchor` once the lowest member PR number is known — ST-4157)."""
        if self.dry_run:
            print(f"[DRY RUN] Would add label '{label}' to PR #{pr_number}")
            return
        self._ensure_labels_exist([label])
        self.github_repo.get_pull(pr_number).add_to_labels(label)
        print(f"🏷️  Added label '{label}' to PR #{pr_number}")

    def find_open_release_anchors(self) -> List[Tuple[int, str]]:
        """Return (number, body) for every OPEN release anchor PR. Used by the idempotency
        guard to detect an existing open release by instanceId.

        Two discovery labels (queried separately — `labels=[...]` is an AND filter): the
        wave-0 anchor (`release:wave:0`) for wave-ordered/standard releases, and the
        anchor-only `release:anchor` for manual-per-stack (ST-4157). Deduped by PR number."""
        anchors: List[Tuple[int, str]] = []
        seen: set = set()
        for label in ("release:wave:0", "release:anchor"):
            for issue in self.github_repo.get_issues(state="open", labels=[label]):
                if issue.pull_request is None:
                    continue
                if issue.number in seen:
                    continue
                seen.add(issue.number)
                pr = self.github_repo.get_pull(issue.number)
                anchors.append((issue.number, pr.body or ""))
        return anchors

    def close_pr(self, number: int) -> None:
        """Close an open PR and delete its head branch (best-effort). Used to clean up the
        already-created lower-wave PRs after a partial fan-out, so no orphaned manifest-less
        release:wave:0 anchor is left behind for the idempotency guard to miss."""
        if self.dry_run:
            print(f"[DRY RUN] Would close PR #{number} and delete its head branch")
            return
        pr = self.github_repo.get_pull(number)
        head_ref = pr.head.ref
        pr.edit(state="closed")
        try:
            self.github_repo.get_git_ref(f"heads/{head_ref}").delete()
        except Exception as exc:
            print(f"  (head branch '{head_ref}' delete skipped: {exc})")

    def _ensure_labels_exist(self, names: List[str]) -> None:
        """Create-if-missing each label (tolerate already-exists)."""
        for name in names:
            try:
                self.github_repo.get_label(name)
            except GithubException as e:
                if e.status == 404:
                    try:
                        self.github_repo.create_label(name=name, color="ededed")
                    except GithubException as ce:
                        if ce.status != 422:  # 422 = already exists (race) → fine
                            raise
                else:
                    raise

    # -----------------------------------------------------------------------------
    # High-Level Combined Operations
    # -----------------------------------------------------------------------------

    def create_branch_commit_and_pr(
        self,
        branch_name: str,
        files_to_commit: List[str],
        commit_message: str,
        pr_title: str,
        pr_body: str,
        base_branch: str = "main",
        auto_merge: bool = False,
        labels: Optional[List[str]] = None,
    ) -> Optional[str]:
        """Create a branch, commit files, and create a PR in one operation.
        
        This combines the common pattern of:
        1. Creating a new branch
        2. Adding and committing files
        3. Pushing the branch
        4. Creating a PR
        
        Note: Files must already exist on disk before calling this method.
        
        Args:
            branch_name: Name for the new branch
            files_to_commit: List of file paths to commit
            commit_message: Commit message
            pr_title: PR title
            pr_body: PR body/description
            base_branch: Base branch for the PR
            auto_merge: If True, attempt to auto-merge
            labels: Optional labels to provision (create-if-missing) and apply

        Returns:
            PR URL if created, None if dry run
        """
        # Always start from the base branch before creating new branch
        print(f"🔀 Switching to base branch: {base_branch}")
        self.checkout_branch(base_branch, create=False)
        
        # Create and checkout branch
        print(f"🌿 Creating new branch: {branch_name} from {base_branch}")
        self.checkout_branch(branch_name, create=True)
        
        # Add and commit files (files should already exist on disk)
        self.add_files(files_to_commit)
        self.commit(commit_message)
        
        # Create PR
        return self.create_pull_request(
            title=pr_title,
            body=pr_body,
            branch_name=branch_name,
            base_branch=base_branch,
            auto_merge=auto_merge,
            labels=labels,
        )
