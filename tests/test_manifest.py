# tests/test_manifest.py
import json
import re
import pytest
from helm_image_updater.manifest import (
    compute_instance_id, build_manifest, build_manual_manifest, manifest_block,
    extract_instance_id, is_manifest_v1, MANIFEST_HEADING,
)

# Mirror the promoter's regexes with exact-JS semantics.
# JS `$` (no multiline flag) does NOT match before a trailing newline; Python's `$` does.
# Use \Z (absolute end-of-string) to match JS behavior exactly.
INSTANCE_ID_RE = re.compile(r"[^\s:@]+\Z")
JSON_FENCE_RE = re.compile(r"```json\r?\n(.*?)```", re.DOTALL)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _valid_manifest(**overrides):
    """Build a fully-valid v1 manifest dict, then apply overrides (or removals)."""
    m = {
        "manifestVersion": "v1",
        "instanceId": "connection-abc123",
        "displayName": "connection@prod",
        "app": "connection",
        "anchorWave": 0,
        "waves": {"0": 10},
        "sourceSha": "deadbeef1234",
        "sourcePr": "https://github.com/org/repo/pull/9",
    }
    for k, v in overrides.items():
        if v is _REMOVE:
            m.pop(k, None)
        else:
            m[k] = v
    return m


class _Sentinel:
    pass


_REMOVE = _Sentinel()  # sentinel for "remove this key"


# ---------------------------------------------------------------------------
# existing tests (unchanged)
# ---------------------------------------------------------------------------

def test_compute_instance_id_from_image_tag():
    # ST-4190: id is '<app>-<image_tag>' — UNIQUE PER FAN-OUT. The source sha is IGNORED
    # (two builds of the same commit must NOT share an instanceId, or the promoter's
    # duplicate-instanceId guard deadlocks both while they are concurrently in-flight).
    assert compute_instance_id("connection", "abcdef0123456789", "production-abc-4448") == "connection-production-abc-4448"
    # Idempotent per exact (app, image_tag) — independent of the sha argument.
    assert compute_instance_id("connection", "deadbeef", "production-abc-4448") == compute_instance_id("connection", None, "production-abc-4448")
    # Two builds of the SAME commit (different tags) → DIFFERENT ids (the deadlock fix).
    assert compute_instance_id("connection", "abcdef", "production-abc-4447") != compute_instance_id("connection", "abcdef", "production-abc-4448")


def test_compute_instance_id_extra_tags_only():
    # ST-4190: an extra-tags-only deploy (image.tag untouched -> empty image_tag) must still
    # yield a UNIQUE, non-degenerate id derived from the extra tag(s), NOT "<app>-".
    a = compute_instance_id("job-queue-daemon", "sha", "", extra_tags=[{"path": "jobQueueRunnerImage.tag", "value": "production-aaa"}])
    b = compute_instance_id("job-queue-daemon", "sha", "", extra_tags=[{"path": "jobQueueRunnerImage.tag", "value": "production-bbb"}])
    assert a != "job-queue-daemon-"          # not degenerate
    assert a != b                            # distinct extra values -> distinct ids
    assert a.startswith("job-queue-daemon-") and INSTANCE_ID_RE.fullmatch(a)
    # Idempotent per exact payload (a re-run of the same extra-tag deploy -> same id).
    assert a == compute_instance_id("job-queue-daemon", "sha", "", extra_tags=[{"path": "jobQueueRunnerImage.tag", "value": "production-aaa"}])


def test_compute_instance_id_image_and_extra_tags():
    # image_tag present + an extra tag -> both folded in; still starts with the readable image id.
    iid = compute_instance_id("connection", "sha", "production-abc-4448", extra_tags=[{"path": "sidecar.tag", "value": "v9"}])
    assert iid.startswith("connection-production-abc-4448-") and INSTANCE_ID_RE.fullmatch(iid)
    # No extra tags -> EXACTLY the image-only contract (unchanged; the e2e #5094 assertion).
    assert compute_instance_id("connection", "sha", "production-abc-4448") == "connection-production-abc-4448"
    assert compute_instance_id("connection", "sha", "production-abc-4448", extra_tags=[]) == "connection-production-abc-4448"


def test_compute_instance_id_no_tags_is_non_degenerate():
    # Defensive: no image_tag AND no extra tags -> fall back to source_sha, never "<app>-".
    assert compute_instance_id("app", "deadbeefcafe00", "") == "app-deadbeefcafe"
    assert compute_instance_id("app", None, "") == "app-notag"


def test_compute_instance_id_is_machine_safe_even_for_unsafe_app():
    # An app name with forbidden chars (defensive) must still yield a valid instanceId.
    iid = compute_instance_id("my app:weird@chart", "deadBEEF00112233", "t")
    assert INSTANCE_ID_RE.fullmatch(iid)


@pytest.mark.parametrize("sha", [None, "", "  ", "Unknown", "unknown"])
def test_compute_instance_id_deterministic_fallback_when_no_sha(sha):
    iid = compute_instance_id("connection", sha, "prod-123")
    assert iid.startswith("connection-") and len(iid) > len("connection-")
    assert INSTANCE_ID_RE.fullmatch(iid)
    # Deterministic: same (app, tag) → same id (so reruns are detectable, F6).
    assert iid == compute_instance_id("connection", None, "prod-123")
    # Different tag → different id (unique per fan-out).
    assert iid != compute_instance_id("connection", None, "prod-999")


def test_build_manifest_shape():
    m = build_manifest(
        app="connection", instance_id="connection-abc", display_name="connection@prod",
        waves={0: 10, 1: 11, 2: 12, 3: 13}, source_sha="abc", source_pr="https://x/pull/9",
    )
    assert m["manifestVersion"] == "v1"
    assert m["anchorWave"] == 0
    assert m["app"] == "connection"
    assert m["instanceId"] == "connection-abc"
    assert m["displayName"] == "connection@prod"
    assert m["waves"] == {"0": 10, "1": 11, "2": 12, "3": 13}  # int wave keys → string keys
    assert m["sourceSha"] == "abc" and m["sourcePr"] == "https://x/pull/9"


def test_build_manifest_omits_absent_optional_source_fields():
    m = build_manifest(app="a", instance_id="a-1", display_name="d", waves={0: 1})
    assert "sourceSha" not in m and "sourcePr" not in m


def test_build_manifest_includes_source_pr_author():
    m = build_manifest(app="a", instance_id="a-1", display_name="a@1",
                       waves={0: 10}, source_pr="https://github.com/keboola/x/pull/1",
                       source_pr_author="vojtabiberle")
    assert m["sourcePrAuthor"] == "vojtabiberle"


def test_build_manifest_omits_source_pr_author_when_absent():
    m = build_manifest(app="a", instance_id="a-1", display_name="a@1", waves={0: 10})
    assert "sourcePrAuthor" not in m


def test_build_manual_manifest_includes_source_pr_author():
    m = build_manual_manifest(app="a", instance_id="a-1", display_name="a@1",
                              members=[10, 11], source_pr_author="odinuv")
    assert m["sourcePrAuthor"] == "odinuv"


def test_is_manifest_v1_accepts_and_rejects_source_pr_author():
    base = build_manifest(app="a", instance_id="a-1", display_name="a@1", waves={0: 10})
    ok = dict(base, sourcePrAuthor="odinuv")
    bad = dict(base, sourcePrAuthor=42)
    assert is_manifest_v1(ok) is True
    assert is_manifest_v1(bad) is False


def test_manifest_block_is_extractable_by_promoter_regex():
    m = build_manifest(app="connection", instance_id="connection-abc", display_name="c@p",
                       waves={0: 10, 1: 11, 2: 12, 3: 13})
    block = manifest_block(m)
    assert MANIFEST_HEADING in block
    fences = JSON_FENCE_RE.findall(block)
    assert len(fences) == 1
    assert json.loads(fences[0]) == m  # round-trips to the exact object


def test_extract_instance_id_reads_first_valid_v1_manifest():
    body = "intro\n\n" + manifest_block(build_manifest(
        app="connection", instance_id="connection-abc", display_name="c", waves={0: 10}))
    assert extract_instance_id(body) == "connection-abc"


@pytest.mark.parametrize("body", [
    None, "", "no fence here",
    "```json\n{\"manifestVersion\": \"v2\"}\n```",          # wrong version
    "```json\n{not json}\n```",                                 # unparseable
    "```json\n{\"manifestVersion\": \"v1\"}\n```",            # missing instanceId
])
def test_extract_instance_id_returns_none_on_bad_body(body):
    assert extract_instance_id(body) is None


# ---------------------------------------------------------------------------
# compute_instance_id must sanitize the image_tag it now embeds (ST-4190)
# ---------------------------------------------------------------------------

def test_compute_instance_id_unsafe_image_tag_is_sanitized():
    """image_tag now feeds the id (ST-4190); any unsafe chars must be sanitized so the
    manifest can never carry an instanceId the promoter would reject."""
    iid = compute_instance_id("connection", "deadbeef", "prod tag:weird@1")
    # Must be a valid instanceId charset — no whitespace, ':', or '@'.
    assert INSTANCE_ID_RE.fullmatch(iid), f"unsafe id: {iid!r}"
    # Must still be prefixed with the safe app name.
    assert iid.startswith("connection-"), f"missing prefix: {iid!r}"


# ---------------------------------------------------------------------------
# Finding 1 + 3: is_manifest_v1 direct test battery
# ---------------------------------------------------------------------------

class TestIsManifestV1:
    # --- accepts ---

    def test_accepts_full_valid_manifest(self):
        assert is_manifest_v1(_valid_manifest()) is True

    def test_accepts_minimal_valid_manifest(self):
        # Only required fields; no optional sourceSha/sourcePr.
        m = {
            "manifestVersion": "v1",
            "instanceId": "connection-abc123",
            "displayName": "connection@prod",
            "app": "connection",
            "anchorWave": 0,
            "waves": {"0": 1},
        }
        assert is_manifest_v1(m) is True

    def test_accepts_empty_string_display_name(self):
        # displayName="" is ACCEPTED (only type-check, not non-empty).
        assert is_manifest_v1(_valid_manifest(displayName="")) is True

    # --- rejects non-dict ---

    @pytest.mark.parametrize("x", [None, [], "x"])
    def test_rejects_non_dict(self, x):
        assert is_manifest_v1(x) is False

    # --- rejects bad manifestVersion ---

    def test_rejects_manifest_version_v2(self):
        assert is_manifest_v1(_valid_manifest(manifestVersion="v2")) is False

    def test_rejects_manifest_version_missing(self):
        assert is_manifest_v1(_valid_manifest(manifestVersion=_REMOVE)) is False

    # --- rejects bad instanceId ---

    @pytest.mark.parametrize("iid", [
        "",       # empty string
        "a b",    # space
        "a:b",    # colon
        "a@b",    # at-sign
        "abc\n",  # trailing newline (JS $ would reject; Python $ would accept — the bug)
        123,      # not a string
    ])
    def test_rejects_bad_instance_id(self, iid):
        assert is_manifest_v1(_valid_manifest(instanceId=iid)) is False

    def test_rejects_instance_id_missing(self):
        assert is_manifest_v1(_valid_manifest(instanceId=_REMOVE)) is False

    # --- rejects bad displayName ---

    def test_rejects_display_name_missing(self):
        assert is_manifest_v1(_valid_manifest(displayName=_REMOVE)) is False

    def test_rejects_display_name_int(self):
        assert is_manifest_v1(_valid_manifest(displayName=5)) is False

    # --- rejects bad app ---

    def test_rejects_app_empty_string(self):
        assert is_manifest_v1(_valid_manifest(app="")) is False

    def test_rejects_app_missing(self):
        assert is_manifest_v1(_valid_manifest(app=_REMOVE)) is False

    # --- rejects bad anchorWave ---

    def test_rejects_anchor_wave_int_nonzero(self):
        assert is_manifest_v1(_valid_manifest(anchorWave=1)) is False

    def test_rejects_anchor_wave_string_zero(self):
        assert is_manifest_v1(_valid_manifest(anchorWave="0")) is False

    def test_rejects_anchor_wave_missing(self):
        assert is_manifest_v1(_valid_manifest(anchorWave=_REMOVE)) is False

    # --- rejects bad waves ---

    def test_rejects_waves_missing(self):
        assert is_manifest_v1(_valid_manifest(waves=_REMOVE)) is False

    def test_rejects_waves_empty_dict(self):
        assert is_manifest_v1(_valid_manifest(waves={})) is False

    def test_rejects_waves_list(self):
        assert is_manifest_v1(_valid_manifest(waves=[])) is False

    def test_rejects_waves_missing_key_0(self):
        # {"1": 2} — no "0" key
        assert is_manifest_v1(_valid_manifest(waves={"1": 2})) is False

    def test_rejects_waves_bad_key_leading_zero(self):
        assert is_manifest_v1(_valid_manifest(waves={"0": 1, "01": 2})) is False

    def test_rejects_waves_bad_key_non_numeric(self):
        assert is_manifest_v1(_valid_manifest(waves={"0": 1, "x": 2})) is False

    def test_rejects_waves_key_with_trailing_newline(self):
        # JS $ rejects "1\n"; Python $ would accept — the bug
        assert is_manifest_v1(_valid_manifest(waves={"0": 1, "1\n": 2})) is False

    def test_rejects_waves_value_zero(self):
        assert is_manifest_v1(_valid_manifest(waves={"0": 0})) is False

    def test_rejects_waves_value_negative(self):
        assert is_manifest_v1(_valid_manifest(waves={"0": -1})) is False

    def test_rejects_waves_value_bool(self):
        assert is_manifest_v1(_valid_manifest(waves={"0": True})) is False

    def test_rejects_waves_duplicate_values(self):
        assert is_manifest_v1(_valid_manifest(waves={"0": 1, "1": 1})) is False

    def test_rejects_waves_value_string(self):
        assert is_manifest_v1(_valid_manifest(waves={"0": "5"})) is False

    # --- rejects bad optional fields ---

    def test_rejects_source_sha_int(self):
        assert is_manifest_v1(_valid_manifest(sourceSha=5)) is False

    def test_rejects_source_pr_int(self):
        assert is_manifest_v1(_valid_manifest(sourcePr=5)) is False

    def test_rejects_source_pr_author_int(self):
        assert is_manifest_v1(_valid_manifest(sourcePrAuthor=5)) is False
