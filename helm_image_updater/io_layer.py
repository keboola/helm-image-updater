"""
I/O Layer for Helm Image Updater

This module contains all I/O operations (file system, Git, GitHub)
separated from business logic. This is the "imperative shell" that
handles all side effects.
"""

import os
import json
from pathlib import Path
from typing import List, Dict, Optional, Any
import yaml
import dpath
from git import Repo
from github import Github
from github.GithubException import GithubException
from time import sleep


class IOLayer:
    """Handles all I/O operations for the application."""
    
    def __init__(self, repo: Repo, github_repo: Any, dry_run: bool = False):
        """Initialize the I/O layer.
        
        Args:
            repo: Git repository object
            github_repo: GitHub repository object
            dry_run: If True, don't perform actual writes
        """
        self.repo = repo
        self.github_repo = github_repo
        self.dry_run = dry_run
    
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
            yaml.dump(data, f)
        
        return True
    
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
        auto_merge: bool = False
    ) -> Optional[str]:
        """Create a GitHub pull request.
        
        Args:
            title: PR title
            body: PR body/description
            branch_name: Head branch name
            base_branch: Base branch name
            auto_merge: If True, attempt to auto-merge
            
        Returns:
            PR URL if created, None if dry run
        """
        if self.dry_run:
            merge_status = "and auto-merge" if auto_merge else "without auto-merge"
            print(f"[DRY RUN] Would create PR: '{title}' {merge_status}")
            print(f"[DRY RUN] Base: {base_branch}, Head: {branch_name}")
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
        
        # Auto-merge if requested
        if auto_merge:
            self._attempt_auto_merge(pr)
        
        return pr.html_url
    
    def _attempt_auto_merge(self, pr, max_retries: int = 5, retry_delay: int = 5):
        """Attempt to auto-merge a PR with retries.
        
        Args:
            pr: GitHub PR object
            max_retries: Maximum number of retry attempts
            retry_delay: Delay between retries in seconds
        """
        for attempt in range(max_retries):
            try:
                pr.update()  # Refresh PR data
                
                if pr.mergeable is None:
                    print(f"PR mergeability not yet determined, waiting... (attempt {attempt + 1}/{max_retries})")
                    sleep(retry_delay)
                    continue
                elif not pr.mergeable:
                    print(f"PR is not mergeable due to conflicts: {pr.html_url}")
                    break
                
                pr.merge()
                print(f"PR automatically merged: {pr.html_url}")
                break
                
            except GithubException as e:
                error_message = str(e.data.get("message", "")).lower()
                if e.status == 405 and "not mergeable" in error_message:
                    if attempt < max_retries - 1:
                        print(f"PR not ready to merge, waiting... (attempt {attempt + 1}/{max_retries})")
                        sleep(retry_delay)
                    else:
                        print(f"Failed to merge PR after {max_retries} attempts: {pr.html_url}")
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
        auto_merge: bool = False
    ) -> Optional[str]:
        """Create a branch, commit files, and create a PR in one operation.
        
        This combines the common pattern of:
        1. Creating a new branch
        2. Adding and committing files
        3. Pushing the branch
        4. Creating a PR
        
        Args:
            branch_name: Name for the new branch
            files_to_commit: List of file paths to commit
            commit_message: Commit message
            pr_title: PR title
            pr_body: PR body/description
            base_branch: Base branch for the PR
            auto_merge: If True, attempt to auto-merge
            
        Returns:
            PR URL if created, None if dry run
        """
        # Create and checkout branch
        self.checkout_branch(branch_name, create=True)
        
        # Add and commit files
        self.add_files(files_to_commit)
        self.commit(commit_message)
        
        # Create PR
        return self.create_pull_request(
            title=pr_title,
            body=pr_body,
            branch_name=branch_name,
            base_branch=base_branch,
            auto_merge=auto_merge
        )
    
        
        return True
    
