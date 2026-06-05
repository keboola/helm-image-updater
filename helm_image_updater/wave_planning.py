"""Pure helpers for promoter-managed (wave) deploys.

No I/O — only data transformation. Consumed by plan_builder.
"""

import hashlib
from typing import Dict, List, Optional

from .models import DeployStrategy
from .stack_classification import classify_stack

# GitHub caps label names at 50 chars; "release:id:" eats 11, leaving 39.
_RELEASE_ID_PREFIX = "release:id:"
_RELEASE_ID_MAX = 50 - len(_RELEASE_ID_PREFIX)  # 39
_HASH_LEN = 12


def compute_release_id(helm_chart: str, image_tag: str) -> str:
    """A stable, collision-resistant, length-bounded grouping key (<chart>-<hash>).

    Promoter treats this as opaque (it gets the app from the PR diff), so the only
    invariants are: fits the label limit, deterministic, unique per (chart, tag).
    """
    digest = hashlib.sha256(f"{helm_chart}\0{image_tag}".encode()).hexdigest()[:_HASH_LEN]
    chart_room = _RELEASE_ID_MAX - 1 - _HASH_LEN  # room for "<chart>-"
    chart = helm_chart[:chart_room]
    return f"{chart}-{digest}"


def release_id_label(release_id: str) -> str:
    return f"{_RELEASE_ID_PREFIX}{release_id}"


def wave_label(wave: int) -> str:
    return f"release:wave:{wave}"


def deploy_label(strategy: DeployStrategy) -> str:
    return f"deploy:{strategy.value}"
