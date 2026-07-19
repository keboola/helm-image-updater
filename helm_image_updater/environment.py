"""
Environment Configuration Module

Handles parsing and validation of environment variables.
This is a pure module - no side effects, just data transformation.
"""

from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple, Any

from .models import DeployStrategy


@dataclass
class EnvironmentConfig:
    """Configuration parsed from environment variables."""
    
    helm_chart: str
    image_tag: str
    github_token: str
    dry_run: bool = False
    # Empty DEPLOY_STRATEGY resolves to STANDARD — the universal default (ST-4131/ST-4159):
    # a PRODUCTION deploy is ALWAYS promoter-managed. There is no legacy fallback and no
    # AUTOMERGE/MULTI_STAGE knob (auto-merge is decided by tag class + target stacks, ST-4169).
    deploy_strategy: DeployStrategy = DeployStrategy.STANDARD
    _deploy_strategy_error: Optional[str] = field(default=None, init=False, repr=False)
    target_path: str = "."
    commit_sha: bool = False
    override_stack: str = ""
    approve_token: str = ""
    extra_tags: List[Dict[str, str]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    _extra_tag_errors: List[int] = field(default_factory=list, init=False, repr=False)
    
    @classmethod
    def from_env(cls, env: Dict[str, str]) -> "EnvironmentConfig":
        """Create configuration from environment variables.
        
        Args:
            env: Dictionary of environment variables (typically os.environ)
            
        Returns:
            EnvironmentConfig instance
        """
        # Parse extra tags
        extra_tags = []
        extra_tag_errors = []  # Track format errors for validation
        for i in range(1, 3):
            if tag_str := env.get(f"EXTRA_TAG{i}", "").strip():
                if ":" in tag_str:
                    path, value = tag_str.split(":", 1)
                    if value.strip():
                        extra_tags.append({"path": path, "value": value.strip()})
                else:
                    # Invalid format - missing colon separator
                    extra_tag_errors.append(i)
        
        # Parse metadata if provided
        metadata = {}
        if metadata_str := env.get("METADATA", "").strip():
            try:
                import base64
                import json
                metadata = json.loads(base64.b64decode(metadata_str))
            except Exception:
                pass  # Ignore invalid metadata
        
        # Parse DEPLOY_STRATEGY. Empty -> standard, the universal default (ST-4131/ST-4159):
        # a PRODUCTION deploy is ALWAYS promoter-managed; there is no legacy fallback.
        # AUTOMERGE is dead (auto-merge is decided by tag class + target stacks, ST-4169)
        # and MULTI_STAGE/cloud_multi_stage was removed (ST-4159) -- old dispatchers still
        # send both, so tolerate-and-ignore (warn for MULTI_STAGE so stragglers surface).
        raw_strategy = env.get("DEPLOY_STRATEGY", "").strip().lower()
        deploy_strategy = DeployStrategy.STANDARD
        deploy_strategy_error = None
        if raw_strategy:
            try:
                deploy_strategy = DeployStrategy(raw_strategy)
            except ValueError:
                deploy_strategy_error = (
                    f"Invalid DEPLOY_STRATEGY: '{raw_strategy}'. "
                    "Must be one of: standard, gradual, critical, "
                    "critical-manual-gate, manual-per-stack, rollback"
                )
        if env.get("MULTI_STAGE", "false").lower() == "true":
            print(
                "WARNING: MULTI_STAGE is deprecated and ignored "
                "(cloud_multi_stage was removed in ST-4159; production deploys are promoter-managed)"
            )

        config = cls(
            helm_chart=env.get("HELM_CHART", ""),
            image_tag=env.get("IMAGE_TAG", "").strip(),
            github_token=env.get("GH_TOKEN", ""),
            dry_run=env.get("DRY_RUN", "false").lower() == "true",
            target_path=env.get("TARGET_PATH", "."),
            commit_sha=env.get("COMMIT_PIPELINE_SHA", "false").lower() == "true",
            override_stack=env.get("OVERRIDE_STACK", "").strip(),
            approve_token=env.get("GH_APPROVE_TOKEN", "").strip(),
            extra_tags=extra_tags,
            metadata=metadata,
            deploy_strategy=deploy_strategy,
        )
        config._extra_tag_errors = extra_tag_errors
        config._deploy_strategy_error = deploy_strategy_error
        return config
    
    def _production_rollout_tag(self) -> str:
        """The tag that decides whether a production rollout (wave / manual-per-stack)
        is valid.

        Mirrors ``plan_builder._determine_strategy`` precedence: ``IMAGE_TAG`` when set,
        otherwise the first non-empty ``EXTRA_TAG`` value. This lets an extra-tags-only
        production deploy (empty ``IMAGE_TAG`` + a production/semver ``EXTRA_TAG``, e.g.
        ``jobQueueRunnerImage.tag:production-…``) drive these strategies, since the
        manifest identity (``compute_instance_id``) and stack selection already support it.
        Returns ``""`` when no tag is carried.
        """
        if self.image_tag:
            return self.image_tag
        for tag in self.extra_tags:
            value = tag.get("value", "")
            if value:
                return value
        return ""

    def validate(self) -> List[str]:
        """Validate the configuration.

        Returns:
            List of error messages (empty if valid)
        """
        from .tag_classification import detect_tag_type, TagType

        errors = []
        
        # Required fields
        if not self.helm_chart:
            errors.append("HELM_CHART is required")
        
        if not self.github_token:
            errors.append("GH_TOKEN is required")
        
        if not self.image_tag and not self.extra_tags:
            errors.append("Either IMAGE_TAG or at least one EXTRA_TAG must be set")
        
        # Validate main image tag format if provided and not in override mode
        if self.image_tag and not self.override_stack:
            tag_type = detect_tag_type(self.image_tag)
            if tag_type == TagType.INVALID:
                errors.append(f"Invalid IMAGE_TAG format: '{self.image_tag}'. Must start with 'dev-', 'production-', 'canary-' or be a valid semver (e.g., 1.2.3)")
        
        # A production override target accepts ONLY production/semver tags. Block any
        # non-production tag -- dev-/canary- OR an unrecognized/`pr-test-*` (INVALID) tag,
        # via the main image OR an extra tag -- from landing on a prod stack. Tag-format
        # validation is SKIPPED in override mode, so INVALID tags reach here; keying on
        # "not production/semver" (rather than a DEV/CANARY allowlist) rejects them too,
        # matching this comment (ST-4169 closes the gap: previously only a dev MAIN tag was
        # caught, so canary/dev-extra/pr-test tags slipped through). Non-production override
        # targets (incl. e2e, which is EXCLUDED_STACKS) are unrestricted -- the pr-test-*/dev
        # -> e2e test-deploy flow is preserved.
        if self.override_stack:
            import os
            from .stack_classification import classify_stack

            # Only validate if the stack actually exists on disk.
            if os.path.isdir(self.override_stack) and classify_stack(self.override_stack).is_production:
                candidate_tags = [self.image_tag] + [t.get("value", "") for t in self.extra_tags]
                if any(detect_tag_type(v) not in (TagType.PRODUCTION, TagType.SEMVER) for v in candidate_tags if v):
                    errors.append("Cannot apply non-production tag to production stack")
        
        if not self.approve_token:
            errors.append("GH_APPROVE_TOKEN is required")

        # Check for extra tag format errors (missing colon)
        for i in self._extra_tag_errors:
            errors.append(f"EXTRA_TAG{i} must be in format 'path:value'")
        
        # Validate extra tag format
        for i, tag in enumerate(self.extra_tags, 1):
            if "path" not in tag or "value" not in tag:
                errors.append(f"EXTRA_TAG{i} must be in format 'path:value'")
            elif not tag["value"]:
                errors.append(f"EXTRA_TAG{i} value cannot be empty")
            elif not self.override_stack:
                # Validate the tag value format
                tag_type = detect_tag_type(tag["value"])
                if tag_type == TagType.INVALID:
                    errors.append(f"Invalid EXTRA_TAG{i} format: '{tag['value']}'. Must start with 'dev-', 'production-', 'canary-' or be a valid semver (e.g., 1.2.3)")

        # DEPLOY_STRATEGY validation
        if self._deploy_strategy_error:
            errors.append(self._deploy_strategy_error)

        if self.deploy_strategy.is_wave:
            rollout_tag = self._production_rollout_tag()
            if self.override_stack:
                errors.append("DEPLOY_STRATEGY wave modes are incompatible with OVERRIDE_STACK")
            elif not rollout_tag:
                errors.append(
                    f"DEPLOY_STRATEGY '{self.deploy_strategy.value}' requires a production/semver "
                    f"IMAGE_TAG or EXTRA_TAG"
                )
            else:
                tag_type = detect_tag_type(rollout_tag)
                if tag_type not in (TagType.PRODUCTION, TagType.SEMVER):
                    errors.append(
                        f"DEPLOY_STRATEGY '{self.deploy_strategy.value}' requires a production/semver "
                        f"IMAGE_TAG or EXTRA_TAG, got '{rollout_tag}'"
                    )

        # manual-per-stack (ST-4157): a production rollout (one PR per prod stack), so it
        # requires a production/semver tag (via IMAGE_TAG or an EXTRA_TAG) and is
        # incompatible with OVERRIDE_STACK — same as the wave strategies (kept separate so
        # its error strings can name the strategy explicitly).
        if self.deploy_strategy == DeployStrategy.MANUAL_PER_STACK:
            rollout_tag = self._production_rollout_tag()
            if self.override_stack:
                errors.append("DEPLOY_STRATEGY manual-per-stack is incompatible with OVERRIDE_STACK")
            elif not rollout_tag:
                errors.append(
                    "DEPLOY_STRATEGY 'manual-per-stack' requires a production/semver IMAGE_TAG or EXTRA_TAG"
                )
            else:
                tag_type = detect_tag_type(rollout_tag)
                if tag_type not in (TagType.PRODUCTION, TagType.SEMVER):
                    errors.append(
                        f"DEPLOY_STRATEGY 'manual-per-stack' requires a production/semver "
                        f"IMAGE_TAG or EXTRA_TAG, got '{rollout_tag}'"
                    )

        # rollback (ST-4277): 1 PR / wave 0 / all changed stacks, never auto-merged —
        # a production rollout like the wave strategies, so it is incompatible with
        # OVERRIDE_STACK and requires a production/semver tag (via IMAGE_TAG or an
        # EXTRA_TAG). This is LOAD-BEARING, not hygiene: a dev-/canary-classified
        # target would otherwise route the plan as a DEV/CANARY deploy and silently
        # skip preemption (single auto-merged PR) -- see RFC ST-4277 §3.2.
        if self.deploy_strategy == DeployStrategy.ROLLBACK:
            rollout_tag = self._production_rollout_tag()
            if self.override_stack:
                errors.append("DEPLOY_STRATEGY rollback is incompatible with OVERRIDE_STACK")
            elif not rollout_tag:
                errors.append(
                    "DEPLOY_STRATEGY 'rollback' requires a production/semver IMAGE_TAG or EXTRA_TAG"
                )
            else:
                tag_type = detect_tag_type(rollout_tag)
                if tag_type not in (TagType.PRODUCTION, TagType.SEMVER):
                    errors.append(
                        f"DEPLOY_STRATEGY 'rollback' requires a production/semver "
                        f"IMAGE_TAG or EXTRA_TAG, got '{rollout_tag}'"
                    )

        return errors