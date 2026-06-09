"""Pure helpers for promoter-managed (wave) deploys.

No I/O — only data transformation. Consumed by plan_builder.

NOTE: compute_release_id / release_id_label (the retired release:id label helpers)
were removed in the ST-4034 manifest-emission refactor. Release grouping/identity now
lives in a JSON manifest in the wave-0 anchor PR body (see manifest.py).
"""

from typing import Dict, List, Optional

from .models import DeployStrategy
from .stack_classification import classify_stack


def wave_label(wave: int) -> str:
    return f"release:wave:{wave}"


def deploy_label(strategy: DeployStrategy) -> str:
    return f"deploy:{strategy.value}"


def resolve_wave(stack: str, metadata: Optional[Dict]) -> int:
    """wave(stack) = explicit `rollout_wave` if present (integer 0..3), else dev->0 / other->3."""
    if metadata and "rollout_wave" in metadata:
        raw = metadata["rollout_wave"]
        if isinstance(raw, bool) or not isinstance(raw, int):
            raise ValueError(f"rollout_wave for {stack} must be an integer 0..3, got {raw!r}")
        if raw < 0 or raw > 3:
            raise ValueError(f"rollout_wave for {stack} must be 0..3, got {raw}")
        return raw
    return 0 if classify_stack(stack).is_dev else 3
