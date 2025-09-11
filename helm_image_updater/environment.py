"""
Environment Configuration Module

Handles parsing and validation of environment variables.
This is a pure module - no side effects, just data transformation.
"""

from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple, Any


@dataclass
class EnvironmentConfig:
    """Configuration parsed from environment variables."""
    
    helm_chart: str
    image_tag: str
    github_token: str
    automerge: bool = True
    dry_run: bool = False
    multi_stage: bool = False
    target_path: str = "."
    commit_sha: bool = False
    override_stack: str = ""
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
        
        config = cls(
            helm_chart=env.get("HELM_CHART", ""),
            image_tag=env.get("IMAGE_TAG", "").strip(),
            github_token=env.get("GH_TOKEN", ""),
            automerge=env.get("AUTOMERGE", "true").lower() == "true",
            dry_run=env.get("DRY_RUN", "false").lower() == "true",
            multi_stage=env.get("MULTI_STAGE", "false").lower() == "true",
            target_path=env.get("TARGET_PATH", "."),
            commit_sha=env.get("COMMIT_PIPELINE_SHA", "false").lower() == "true",
            override_stack=env.get("OVERRIDE_STACK", "").strip(),
            extra_tags=extra_tags,
            metadata=metadata
        )
        config._extra_tag_errors = extra_tag_errors
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
        
        return errors