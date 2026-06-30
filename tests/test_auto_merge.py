"""Tests for the tag-type / stack-based auto-merge decision (ST-4169)."""

from helm_image_updater.plan_builder import _effective_tag_type
from helm_image_updater.models import UpdatePlan, UpdateStrategy
from helm_image_updater.tag_classification import TagType


def _plan(image_tag="", extra=None):
    return UpdatePlan(
        strategy=UpdateStrategy.DEV,
        helm_chart="x",
        image_tag=image_tag,
        extra_tags=extra or [],
    )


# --- Task 1: _effective_tag_type (most-cautious tag class) ---

def test_effective_tag_type_dev():
    assert _effective_tag_type(_plan("dev-abc")) == TagType.DEV


def test_effective_tag_type_canary():
    assert _effective_tag_type(_plan("canary-orion-abc")) == TagType.CANARY


def test_effective_tag_type_mixed_dev_image_prod_extra_is_production():
    # most-cautious: a production extra tag dominates a dev image tag
    p = _plan("dev-abc", [{"path": "x.tag", "value": "production-def"}])
    assert _effective_tag_type(p) == TagType.PRODUCTION


def test_effective_tag_type_semver_is_production_class():
    # semver collapses to PRODUCTION (most-cautious) -- it is a production release.
    assert _effective_tag_type(_plan("1.2.3")) == TagType.PRODUCTION


def test_effective_tag_type_pr_test_is_invalid():
    # unrecognized tags (pr-test-*) are not production-class -> eligible to auto-merge
    # on a non-prod stack via the override flow.
    assert _effective_tag_type(_plan("pr-test-7786-deadbeef")) == TagType.INVALID
