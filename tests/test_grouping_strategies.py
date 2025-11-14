"""Tests for the grouping strategies implementation."""

import os
import pytest
from unittest.mock import Mock, MagicMock, patch
from helm_image_updater.models import GroupingStrategy, PRType, UpdateStrategy
from helm_image_updater.environment import EnvironmentConfig
from helm_image_updater.grouping_strategies import (
    GroupingStrategyHandler,
    GroupingContext,
    should_auto_merge
)


class TestGroupingStrategyHandler:
    """Test the GroupingStrategyHandler class."""

    def setup_method(self):
        """Setup test fixtures."""
        self.handler = GroupingStrategyHandler()

    def create_context(self,
                       grouping_strategy=GroupingStrategy.LEGACY,
                       update_strategy=UpdateStrategy.PRODUCTION,
                       automerge=True,
                       multi_stage=False,
                       stack_count=3):
        """Helper to create a GroupingContext for testing."""
        config = Mock(spec=EnvironmentConfig)
        config.grouping_strategy = grouping_strategy
        config.automerge = automerge
        config.multi_stage = multi_stage

        plan = Mock()
        plan.strategy = update_strategy
        plan.override_stack = None
        plan.image_tag = "production-1.2.3"

        # Create mock stack changes
        stack_changes = []
        for i in range(stack_count):
            stack_changes.append({
                'stack': f'stack-{i}',
                'file_change': Mock(),
                'changes': []
            })

        io_layer = Mock()
        env = {}

        return GroupingContext(
            config=config,
            plan=plan,
            stack_changes=stack_changes,
            io_layer=io_layer,
            env=env
        )


class TestLegacyBackwardsCompatibility:
    """Test that LEGACY mode exactly matches old behavior."""

    def setup_method(self):
        """Setup test fixtures."""
        self.handler = GroupingStrategyHandler()

    def test_legacy_dev_tag_always_single_pr(self):
        """Test that dev tags always create single PR in LEGACY mode."""
        # Test with automerge=true
        context = self.create_legacy_context(
            update_strategy=UpdateStrategy.DEV,
            automerge=True,
            stack_count=5
        )
        groups = self.handler.group_changes(context)
        assert len(groups) == 1
        assert len(groups[0]['stacks']) == 5

        # Test with automerge=false (should still be single PR)
        context = self.create_legacy_context(
            update_strategy=UpdateStrategy.DEV,
            automerge=False,
            stack_count=5
        )
        groups = self.handler.group_changes(context)
        assert len(groups) == 1
        assert len(groups[0]['stacks']) == 5

    def test_legacy_production_tag_with_automerge_true(self):
        """Test production tags with automerge=true create single PR."""
        context = self.create_legacy_context(
            update_strategy=UpdateStrategy.PRODUCTION,
            automerge=True,
            stack_count=20
        )
        groups = self.handler.group_changes(context)
        assert len(groups) == 1
        assert len(groups[0]['stacks']) == 20
        assert groups[0]['pr_type'] == PRType.STANDARD.value

    def test_legacy_production_tag_with_automerge_false(self):
        """Test production tags with automerge=false create multiple PRs."""
        context = self.create_legacy_context(
            update_strategy=UpdateStrategy.PRODUCTION,
            automerge=False,
            stack_count=20
        )
        groups = self.handler.group_changes(context)
        assert len(groups) == 20  # One PR per stack
        for group in groups:
            assert len(group['stacks']) == 1
            assert group['pr_type'] == PRType.STANDARD.value

    def test_legacy_canary_always_single_pr(self):
        """Test that canary deployments always create single PR."""
        context = self.create_legacy_context(
            update_strategy=UpdateStrategy.CANARY,
            automerge=False,  # Should be ignored
            stack_count=1
        )
        groups = self.handler.group_changes(context)
        assert len(groups) == 1
        assert groups[0]['pr_type'] == PRType.CANARY.value

    def test_legacy_multi_stage_true(self):
        """Test MULTI_STAGE=true creates cloud×stage groups."""
        context = self.create_legacy_context(
            update_strategy=UpdateStrategy.PRODUCTION,
            multi_stage=True,
            stack_count=10
        )

        # Mock cloud detection
        with patch('helm_image_updater.grouping_strategies.get_stack_cloud_provider') as mock_cloud:
            with patch('helm_image_updater.grouping_strategies.classify_stack') as mock_classify:
                # Setup mocks to return different clouds and dev/prod
                def cloud_side_effect(stack, io_layer):
                    if 'stack-0' in stack or 'stack-3' in stack:
                        return 'aws'
                    elif 'stack-1' in stack or 'stack-4' in stack:
                        return 'azure'
                    else:
                        return 'gcp'

                def classify_side_effect(stack):
                    result = Mock()
                    result.is_dev = int(stack.split('-')[1]) < 5
                    return result

                mock_cloud.side_effect = cloud_side_effect
                mock_classify.side_effect = classify_side_effect

                groups = self.handler.group_changes(context)

                # Should create up to 6 groups (3 clouds × 2 stages)
                assert len(groups) <= 6
                for group in groups:
                    assert group['pr_type'] in [
                        PRType.MULTI_STAGE_DEV.value,
                        PRType.MULTI_STAGE_PROD.value
                    ]

    def create_legacy_context(self,
                             update_strategy=UpdateStrategy.PRODUCTION,
                             automerge=True,
                             multi_stage=False,
                             stack_count=3):
        """Helper to create a LEGACY GroupingContext for testing."""
        config = Mock(spec=EnvironmentConfig)
        config.grouping_strategy = GroupingStrategy.LEGACY
        config.automerge = automerge
        config.multi_stage = multi_stage

        plan = Mock()
        plan.strategy = update_strategy
        plan.override_stack = None
        plan.image_tag = f"{update_strategy.value}-1.2.3"

        # Create mock stack changes
        stack_changes = []
        for i in range(stack_count):
            stack_changes.append({
                'stack': f'stack-{i}',
                'file_change': Mock(),
                'changes': []
            })

        io_layer = Mock()
        env = {}

        return GroupingContext(
            config=config,
            plan=plan,
            stack_changes=stack_changes,
            io_layer=io_layer,
            env=env
        )


class TestModernStrategies:
    """Test the modern grouping strategies."""

    def setup_method(self):
        """Setup test fixtures."""
        self.handler = GroupingStrategyHandler()

    def test_single_strategy_always_one_pr(self):
        """Test SINGLE strategy always creates one PR."""
        # Test with different automerge settings
        for automerge in [True, False]:
            context = self.create_context(
                grouping_strategy=GroupingStrategy.SINGLE,
                automerge=automerge,
                stack_count=20
            )
            groups = self.handler.group_changes(context)
            assert len(groups) == 1
            assert len(groups[0]['stacks']) == 20

    def test_stack_strategy_always_per_stack(self):
        """Test STACK strategy always creates per-stack PRs."""
        # Test with different automerge settings
        for automerge in [True, False]:
            context = self.create_context(
                grouping_strategy=GroupingStrategy.STACK,
                automerge=automerge,
                stack_count=20
            )
            groups = self.handler.group_changes(context)
            assert len(groups) == 20
            for group in groups:
                assert len(group['stacks']) == 1

    def test_cloud_multi_stage_requires_production(self):
        """Test CLOUD_MULTI_STAGE requires production tags."""
        # Should fall back to SINGLE for dev tags
        context = self.create_context(
            grouping_strategy=GroupingStrategy.CLOUD_MULTI_STAGE,
            update_strategy=UpdateStrategy.DEV,
            stack_count=5
        )
        groups = self.handler.group_changes(context)
        assert len(groups) == 1  # Falls back to single

    def test_override_stack_overrides_strategy(self):
        """Test that OVERRIDE_STACK overrides any grouping strategy."""
        for strategy in GroupingStrategy:
            context = self.create_context(
                grouping_strategy=strategy,
                stack_count=1
            )
            context.plan.override_stack = "override-stack"

            groups = self.handler.group_changes(context)
            assert len(groups) == 1

    def create_context(self,
                       grouping_strategy=GroupingStrategy.SINGLE,
                       update_strategy=UpdateStrategy.PRODUCTION,
                       automerge=True,
                       stack_count=3):
        """Helper to create a GroupingContext for testing."""
        config = Mock(spec=EnvironmentConfig)
        config.grouping_strategy = grouping_strategy
        config.automerge = automerge
        config.multi_stage = False

        plan = Mock()
        plan.strategy = update_strategy
        plan.override_stack = None
        plan.image_tag = f"{update_strategy.value}-1.2.3"

        # Create mock stack changes
        stack_changes = []
        for i in range(stack_count):
            stack_changes.append({
                'stack': f'stack-{i}',
                'file_change': Mock(),
                'changes': []
            })

        io_layer = Mock()
        env = {}

        return GroupingContext(
            config=config,
            plan=plan,
            stack_changes=stack_changes,
            io_layer=io_layer,
            env=env
        )


class TestAutoMergeLogic:
    """Test the should_auto_merge function."""

    def test_canary_always_auto_merges(self):
        """Test that canary PRs always auto-merge."""
        assert should_auto_merge(PRType.CANARY, False, GroupingStrategy.LEGACY) is True
        assert should_auto_merge(PRType.CANARY, True, GroupingStrategy.SINGLE) is True

    def test_multi_stage_prod_never_auto_merges(self):
        """Test that multi-stage production PRs never auto-merge."""
        assert should_auto_merge(PRType.MULTI_STAGE_PROD, True, GroupingStrategy.CLOUD_MULTI_STAGE) is False
        assert should_auto_merge(PRType.MULTI_STAGE_PROD, False, GroupingStrategy.LEGACY) is False

    def test_standard_respects_user_preference(self):
        """Test that standard PRs respect user preference."""
        assert should_auto_merge(PRType.STANDARD, True, GroupingStrategy.SINGLE) is True
        assert should_auto_merge(PRType.STANDARD, False, GroupingStrategy.SINGLE) is False

    def test_multi_stage_dev_respects_user_preference(self):
        """Test that multi-stage dev PRs respect user preference."""
        assert should_auto_merge(PRType.MULTI_STAGE_DEV, True, GroupingStrategy.CLOUD_MULTI_STAGE) is True
        assert should_auto_merge(PRType.MULTI_STAGE_DEV, False, GroupingStrategy.CLOUD_MULTI_STAGE) is False