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


class DeployStrategy(Enum):
    """Deploy strategy (the DEPLOY_STRATEGY knob). Values double as the `deploy:*`
    label value for promoter-managed (wave) strategies."""
    STANDARD = "standard"
    GRADUAL = "gradual"
    CRITICAL = "critical"
    CRITICAL_MANUAL_GATE = "critical-manual-gate"
    MANUAL_PER_STACK = "manual-per-stack"

    @property
    def is_wave(self) -> bool:
        """Strategies routed through the strict waves-0..3 grouping
        (`_group_changes_by_wave`). MUST exclude STANDARD — its 2-wave dev→prod
        grouping is contiguous-from-0 by construction and must not hit the
        0..3-required guard. MUST also exclude MANUAL_PER_STACK (ST-4157) — it has
        NO waves (one PR per stack, flat member set)."""
        return self in (
            DeployStrategy.GRADUAL,
            DeployStrategy.CRITICAL,
            DeployStrategy.CRITICAL_MANUAL_GATE,
        )

    @property
    def is_promoter_managed(self) -> bool:
        """Strategy-level CAPABILITY: EVERY strategy is promoter-capable (ST-4159 removed
        the only non-promoter strategy, cloud_multi_stage). PRs are created unmerged,
        labelled, carrying a manifest. Superset of `is_wave` (adds STANDARD 2-wave dev→prod
        and MANUAL_PER_STACK one-PR-per-stack).

        NOTE: this is about the STRATEGY, not a given run. Whether a specific run is
        actually promoter-managed is gated at the PLAN level, not the strategy level: only
        a PRODUCTION deploy is staged — DEV/CANARY/OVERRIDE runs keep their own single-PR
        auto-merged handling. The run-time condition lives in
        `plan_builder._is_promoter_managed_standard` / `_is_promoter_managed_manual_per_stack`."""
        return self.is_wave or self in (DeployStrategy.STANDARD, DeployStrategy.MANUAL_PER_STACK)


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
    labels: List[str] = field(default_factory=list)
    wave_number: Optional[int] = None  # set only for pr_type == 'wave'
    # manual-per-stack (ST-4157): True for a member PR (pr_type == 'manual'). The executor
    # collects these, anchors the lowest-numbered one (adds release:anchor + the manifest).
    manual_member: bool = False


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
    override_stack: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    manifest_context: Optional[Dict[str, Any]] = None  # {app, instance_id, display_name, source_sha, source_pr}; wave mode only

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