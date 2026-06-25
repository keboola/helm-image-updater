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
    automerge: bool = True
    dry_run: bool = False
    multi_stage: bool = False
    deploy_strategy: DeployStrategy = DeployStrategy.STANDARD
    # True for an EXPLICIT DEPLOY_STRATEGY=standard run: the promoter-managed 2-wave
    # dev→prod path (ST-4126). AUTOMERGE is IGNORED, exactly like the wave strategies. The
    # legacy default (empty strategy → STANDARD) leaves this False so historical single-PR /
    # per-stack behaviour is unchanged.
    promoter_managed_standard: bool = False
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
        
        # Parse DEPLOY_STRATEGY (default standard). MULTI_STAGE=true is a deprecated
        # alias for cloud_multi_stage when DEPLOY_STRATEGY is not explicitly set.
        raw_strategy = env.get("DEPLOY_STRATEGY", "").strip().lower()
        multi_stage_raw = env.get("MULTI_STAGE", "false").lower() == "true"
        deploy_strategy = DeployStrategy.STANDARD
        deploy_strategy_error = None
        if raw_strategy:
            try:
                deploy_strategy = DeployStrategy(raw_strategy)
            except ValueError:
                deploy_strategy_error = (
                    f"Invalid DEPLOY_STRATEGY: '{raw_strategy}'. "
                    "Must be one of: standard, cloud_multi_stage, gradual, critical, critical-manual-gate"
                )
            if multi_stage_raw and deploy_strategy != DeployStrategy.CLOUD_MULTI_STAGE:
                print("WARNING: MULTI_STAGE=true is ignored because DEPLOY_STRATEGY is set explicitly")
        elif multi_stage_raw:
            deploy_strategy = DeployStrategy.CLOUD_MULTI_STAGE

        # Keep the legacy `multi_stage` flag in sync so the existing cloud×stage grouping
        # branch (which keys off plan.multi_stage) fires for DEPLOY_STRATEGY=cloud_multi_stage too.
        multi_stage = deploy_strategy == DeployStrategy.CLOUD_MULTI_STAGE

        automerge = env.get("AUTOMERGE", "true").lower() == "true"

        # Promoter-managed `standard` (ST-4126): an EXPLICIT DEPLOY_STRATEGY=standard always
        # emits the 2-wave dev→prod release — AUTOMERGE is IGNORED, exactly like the wave
        # strategies (gradual/critical/...). The wave PRs are created unmerged (auto_merge=False
        # ⇒ HIU auto-approves them) and merged later by release-promoter. `raw_strategy` is the
        # empty-string default for the action's unset deploy-strategy, so the legacy default
        # (empty → STANDARD) never trips this — it stays the historical single-PR / per-stack flow.
        promoter_managed_standard = (
            raw_strategy == "standard"
            and deploy_strategy == DeployStrategy.STANDARD
        )

        config = cls(
            helm_chart=env.get("HELM_CHART", ""),
            image_tag=env.get("IMAGE_TAG", "").strip(),
            github_token=env.get("GH_TOKEN", ""),
            automerge=automerge,
            dry_run=env.get("DRY_RUN", "false").lower() == "true",
            multi_stage=multi_stage,
            promoter_managed_standard=promoter_managed_standard,
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
        
        # Check for dev tag on production stack (even with override)
        if self.override_stack and self.image_tag:
            import os
            from .stack_classification import classify_stack
            
            # Only validate if the stack actually exists on disk
            if os.path.isdir(self.override_stack):
                tag_type = detect_tag_type(self.image_tag)
                stack_classification = classify_stack(self.override_stack)
                # Check if it's a dev tag being applied to a production stack
                if tag_type == TagType.DEV and stack_classification.is_production:
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
            if self.override_stack:
                errors.append("DEPLOY_STRATEGY wave modes are incompatible with OVERRIDE_STACK")
            elif not self.image_tag:
                errors.append(
                    f"DEPLOY_STRATEGY '{self.deploy_strategy.value}' requires a production/semver IMAGE_TAG"
                )
            else:
                tag_type = detect_tag_type(self.image_tag)
                if tag_type not in (TagType.PRODUCTION, TagType.SEMVER):
                    errors.append(
                        f"DEPLOY_STRATEGY '{self.deploy_strategy.value}' requires a production/semver "
                        f"IMAGE_TAG, got '{self.image_tag}'"
                    )

        return errors