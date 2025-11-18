"""
Grouping Strategy Handler

Handles the logic for grouping stack changes into pull requests based on
the selected grouping strategy. Separates grouping concerns from merge behavior.
"""

import logging
from typing import List, Dict, Any, Optional
from dataclasses import dataclass

from .models import GroupingStrategy, PRType, UpdateStrategy
from .environment import EnvironmentConfig
from .cloud_detection import get_stack_cloud_provider
from .stack_classification import classify_stack

logger = logging.getLogger(__name__)


@dataclass
class GroupingContext:
    """Context information needed for grouping decisions."""
    config: EnvironmentConfig
    plan: Any  # UpdatePlan - using Any to avoid circular import
    stack_changes: List[Dict[str, Any]]
    io_layer: Any  # IOLayer - using Any to avoid circular import
    env: Dict[str, str]  # Original environment variables


class GroupingStrategyHandler:
    """Handles PR grouping based on selected strategy."""

    def __init__(self):
        self.logger = logger

    def group_changes(self,
                     context: GroupingContext) -> List[Dict[str, Any]]:
        """
        Main entry point for grouping changes into PRs.

        Args:
            context: Context containing all necessary information

        Returns:
            List of PR groups to create
        """
        strategy = context.config.grouping_strategy
        self.logger.info(f"Applying grouping strategy: {strategy.value}")

        # Handle special cases that override strategy
        if context.plan.override_stack:
            self.logger.info(f"OVERRIDE_STACK={context.plan.override_stack} overrides grouping strategy")
            return self._apply_single_grouping(context)

        # Apply the selected strategy
        if strategy == GroupingStrategy.LEGACY:
            return self._apply_legacy_grouping(context)
        elif strategy == GroupingStrategy.SINGLE:
            return self._apply_single_grouping(context)
        elif strategy == GroupingStrategy.STACK:
            return self._apply_stack_grouping(context)
        elif strategy == GroupingStrategy.CLOUD_MULTI_STAGE:
            return self._apply_cloud_multi_stage_grouping(context)
        else:
            raise ValueError(f"Unhandled strategy: {strategy}")

    def _apply_legacy_grouping(self, context: GroupingContext) -> List[Dict[str, Any]]:
        """
        Implements current production behavior for perfect backwards compatibility.
        This is the DEFAULT to ensure zero breaking changes.
        """
        self.logger.debug("Applying LEGACY grouping strategy")
        plan = context.plan
        config = context.config
        stack_changes = context.stack_changes

        # Special case: Canary deployment
        if plan.strategy == UpdateStrategy.CANARY:
            self.logger.info("Canary deployment, creating single PR")
            canary_base = self._get_canary_base_branch(plan.image_tag)
            return self._create_single_pr_group(stack_changes, canary_base, PRType.CANARY)

        # Multi-stage mode (old MULTI_STAGE=true behavior)
        if config.multi_stage:
            self.logger.info("MULTI_STAGE=true (legacy), applying cloud×stage grouping")
            return self._apply_cloud_multi_stage_grouping(context)

        # Dev tags: Always single PR (legacy behavior ignores automerge)
        if plan.strategy == UpdateStrategy.DEV:
            self.logger.info("Dev tag detected, creating single PR (legacy behavior ignores automerge)")
            return self._create_single_pr_group(stack_changes, 'main', PRType.STANDARD)

        # Production tags: Based on automerge
        if plan.strategy == UpdateStrategy.PRODUCTION:
            if config.automerge:
                self.logger.info("Production tag with automerge=true, creating single PR")
                return self._create_single_pr_group(stack_changes, 'main', PRType.STANDARD)
            else:
                self.logger.info(f"Production tag with automerge=false, creating {len(stack_changes)} PRs")
                return self._create_per_stack_groups(stack_changes)

        # Default fallback
        self.logger.warning(f"Unhandled case in legacy grouping for strategy {plan.strategy}, defaulting to single PR")
        return self._create_single_pr_group(stack_changes, 'main', PRType.STANDARD)

    def _apply_single_grouping(self, context: GroupingContext) -> List[Dict[str, Any]]:
        """All stacks in one PR."""
        stack_changes = context.stack_changes
        self.logger.debug(f"Creating single PR for {len(stack_changes)} stacks")
        return self._create_single_pr_group(stack_changes, 'main', PRType.STANDARD)

    def _apply_stack_grouping(self, context: GroupingContext) -> List[Dict[str, Any]]:
        """One PR per stack."""
        stack_changes = context.stack_changes
        self.logger.debug(f"Creating {len(stack_changes)} individual PRs")
        return self._create_per_stack_groups(stack_changes)

    def _apply_cloud_multi_stage_grouping(self, context: GroupingContext) -> List[Dict[str, Any]]:
        """Group by cloud provider and stage."""
        plan = context.plan
        stack_changes = context.stack_changes
        io_layer = context.io_layer

        # Validate strategy is appropriate for tag type
        if plan.strategy != UpdateStrategy.PRODUCTION:
            self.logger.warning(
                f"CLOUD_MULTI_STAGE strategy requires production tag, got {plan.strategy}. "
                f"Falling back to SINGLE grouping."
            )
            return self._apply_single_grouping(context)

        self.logger.info("🔄 Multi-stage deployment - grouping by cloud and dev/prod")

        # Group by (cloud, stage) tuple
        cloud_groups = {
            ("aws", "dev"): [],
            ("aws", "prod"): [],
            ("azure", "dev"): [],
            ("azure", "prod"): [],
            ("gcp", "dev"): [],
            ("gcp", "prod"): []
        }

        # Group changes by cloud and stage
        self.logger.info(f"📋 Analyzing {len(stack_changes)} stack changes:")
        for sc in stack_changes:
            stack = sc['stack']
            cloud = get_stack_cloud_provider(stack, io_layer)
            stack_info = classify_stack(stack)
            stage = "dev" if stack_info.is_dev else "prod"

            cloud_groups[(cloud, stage)].append(sc)
            self.logger.info(f"  - {stack} → {cloud} {stage}")

        # Create PR groups for non-empty combinations
        # Production PRs first, then dev PRs to prevent race condition
        groups = []
        self.logger.info("\n🎯 Creating PR groups (prod first, then dev):")
        for stage in ["prod", "dev"]:
            for cloud in ["aws", "azure", "gcp"]:
                changes = cloud_groups[(cloud, stage)]
                if changes:  # Only create PR if there are changes
                    stacks = [sc['stack'] for sc in changes]
                    pr_type = PRType.MULTI_STAGE_DEV if stage == "dev" else PRType.MULTI_STAGE_PROD
                    self.logger.info(f"  - {cloud} {stage}: {len(changes)} changes in stacks {stacks}")
                    groups.append({
                        'stacks': stacks,
                        'changes': changes,
                        'base_branch': 'main',
                        'pr_type': pr_type.value,
                        'cloud_provider': cloud
                    })

        self.logger.info(f"📊 Total PR groups created: {len(groups)}")
        return groups

    # Helper methods for creating PR groups
    def _create_single_pr_group(self,
                               stack_changes: List[Dict[str, Any]],
                               base_branch: str,
                               pr_type: PRType) -> List[Dict[str, Any]]:
        """Create a single PR group containing all stacks."""
        return [{
            'stacks': [sc['stack'] for sc in stack_changes],
            'changes': stack_changes,
            'base_branch': base_branch,
            'pr_type': pr_type.value
        }]

    def _create_per_stack_groups(self,
                                stack_changes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Create one PR group per stack."""
        return [
            {
                'stacks': [sc['stack']],
                'changes': [sc],
                'base_branch': 'main',
                'pr_type': PRType.STANDARD.value
            }
            for sc in stack_changes
        ]

    def _get_canary_base_branch(self, image_tag: str) -> str:
        """Get the base branch for a canary deployment."""
        from .config import CANARY_STACKS

        if image_tag and image_tag.startswith("canary-"):
            canary_tag_prefix = f"canary-{image_tag.split('-')[1]}" if len(image_tag.split('-')) > 1 else ""
            for prefix, canary_config in CANARY_STACKS.items():
                if prefix == canary_tag_prefix:
                    return canary_config["base_branch"]
        return "main"


def should_auto_merge(pr_type: PRType,
                     user_requested: bool,
                     strategy: GroupingStrategy) -> bool:
    """
    Determine if a PR should be auto-merged.
    Now ONLY controls merge behavior, not grouping.

    Args:
        pr_type: Type of PR being created
        user_requested: User's automerge preference
        strategy: Grouping strategy (for logging)

    Returns:
        Whether to auto-merge the PR
    """
    # Special case: Canary always auto-merges
    if pr_type == PRType.CANARY:
        logger.info("Canary PR always auto-merges")
        return True

    # Special case: Multi-stage prod never auto-merges
    if pr_type == PRType.MULTI_STAGE_PROD:
        logger.info("Multi-stage production PR never auto-merges (safety rule)")
        return False

    # Otherwise respect user preference
    logger.debug(f"Using user preference for auto-merge: {user_requested}")
    return user_requested