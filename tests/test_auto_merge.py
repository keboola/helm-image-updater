"""Tests for the tag-type / stack-based auto-merge decision (ST-4169)."""

from helm_image_updater.plan_builder import _effective_tag_type, _should_auto_merge
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


# --- Task 2: _should_auto_merge (non-production deploy -> non-prod stacks only) ---

DEV = ["dev-keboola-gcp-us-central1"]      # is_dev
E2E = ["dev-keboola-gcp-us-east1-e2e"]     # is_excluded (e2e)
PROD = ["cloud-keboola-acme"]              # unknown name -> is_production


def test_dev_tag_dev_stack_auto_merges():
    assert _should_auto_merge(_plan("dev-abc"), "standard", DEV) is True


def test_canary_auto_merges():
    assert _should_auto_merge(_plan("canary-orion-abc"), "canary",
                              ["dev-keboola-canary-orion"]) is True


def test_production_does_not_auto_merge():
    assert _should_auto_merge(_plan("production-abc"), "standard", PROD) is False


def test_semver_does_not_auto_merge():
    assert _should_auto_merge(_plan("1.2.3"), "standard", PROD) is False


def test_wave_and_manual_never_auto_merge():
    assert _should_auto_merge(_plan("dev-abc"), "wave", DEV) is False
    assert _should_auto_merge(_plan("dev-abc"), "manual", DEV) is False


def test_dev_tag_on_prod_stack_does_not_auto_merge():
    # Defense-in-depth: validate() should reject this run, but if a dev tag ever
    # reaches a production stack, never auto-merge it.
    assert _should_auto_merge(_plan("dev-abc"), "standard", PROD) is False


def test_override_dev_to_e2e_auto_merges():
    p = _plan("dev-abc"); p.strategy = UpdateStrategy.OVERRIDE
    assert _should_auto_merge(p, "standard", E2E) is True


def test_override_production_does_not_auto_merge():
    p = _plan("production-abc"); p.strategy = UpdateStrategy.OVERRIDE
    assert _should_auto_merge(p, "standard", PROD) is False


def test_override_pr_test_tag_to_dev_auto_merges():
    # connection PR-test deploy: an unrecognized `pr-test-*` image on a dev stack via
    # override-stack must still auto-merge (regression guard for ST-4169).
    p = _plan("pr-test-7786-deadbeef"); p.strategy = UpdateStrategy.OVERRIDE
    assert _should_auto_merge(p, "standard", DEV) is True


def test_semver_on_dev_stack_does_not_auto_merge():
    # production-class (semver) image is never auto-merged, even onto a dev stack.
    assert _should_auto_merge(_plan("1.2.3"), "standard", DEV) is False
