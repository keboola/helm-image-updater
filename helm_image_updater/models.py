"""Data models for planning and execution separation."""

from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from enum import Enum


class UpdateStrategy(Enum):
    """Update strategy based on tag type."""
    DEV = "dev"
    PRODUCTION = "production"
    CANARY = "canary"
    OVERRIDE = "override"
    INVALID = "invalid"


class GroupingStrategy(Enum):
    """Strategies for grouping stacks into pull requests."""
    LEGACY = "legacy"                      # Current behavior (default for compatibility)
    SINGLE = "single"                      # One PR for all stacks
    STACK = "stack"                        # One PR per stack
    CLOUD_MULTI_STAGE = "cloud-multi-stage"  # Cloud×Stage matrix (replaces MULTI_STAGE)


class PRType(Enum):
    """Types of pull requests with specific merge behaviors."""
    STANDARD = "standard"                  # Regular PR
    MULTI_STAGE_DEV = "multi_stage_dev"   # Multi-stage dev PR (can auto-merge)
    MULTI_STAGE_PROD = "multi_stage_prod" # Multi-stage prod PR (never auto-merges)
    CANARY = "canary"                     # Canary PR (always auto-merges)


@dataclass
class TagChange:
    """Represents a change to be made to a tag."""
    path: str
    old_value: Any
    new_value: Any
    change_type: str  # 'image_tag', 'extra_tag', 'commit_sha'


@dataclass
class FileChange:
    """Represents a single file change to be made."""
    file_path: str
    old_content: str
    new_content: str
    change_description: str  # Human-readable description


@dataclass
class PRPlan:
    """Represents a pull request to be created."""
    branch_name: str
    pr_title: str
    pr_body: str
    base_branch: str
    auto_merge: bool
    files_to_commit: List[str]  # List of file paths that will be committed
    commit_message: str
    pr_type: PRType = PRType.STANDARD  # Type of PR for merge behavior rules
    cloud_provider: Optional[str] = None  # For cloud-specific PRs
    
    
@dataclass
class UpdatePlan:
    """Complete plan for the update operation."""
    # Configuration
    strategy: UpdateStrategy
    helm_chart: str
    image_tag: str
    extra_tags: List[Dict[str, str]] = field(default_factory=list)
    
    # Discovered context
    target_stacks: List[str] = field(default_factory=list)
    excluded_stacks: List[str] = field(default_factory=list)
    
    # Concrete changes to make
    file_changes: List[FileChange] = field(default_factory=list)
    pr_plans: List[PRPlan] = field(default_factory=list)
    
    # Metadata
    dry_run: bool = False
    multi_stage: bool = False
    override_stack: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def has_changes(self) -> bool:
        """Check if there are any changes to make."""
        return bool(self.file_changes)
    
    def get_affected_files(self) -> List[str]:
        """Get list of all files that will be modified."""
        return list({fc.file_path for fc in self.file_changes})


@dataclass
class ExecutionResult:
    """Result of executing an update plan."""
    success: bool
    pr_urls: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    files_written: List[str] = field(default_factory=list)
    changes_made: List[str] = field(default_factory=list)
    dry_run: bool = False