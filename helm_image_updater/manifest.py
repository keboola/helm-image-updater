"""Pure helpers for the release manifest embedded in the wave-0 anchor PR body.

The release-promoter groups a release by a JSON manifest in the wave-0 PR body
(NOT by a release:id label). This module builds that manifest, renders the
markdown block, and extracts an instanceId from an existing body for HIU's
idempotency guard. No I/O.

Mirrors release-promoter src/core/interpret/manifest.ts (extractManifest /
isManifestV1) — keep in sync if the promoter contract changes.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, Optional

MANIFEST_HEADING = "## ⚠️ Release manifest — DO NOT EDIT (machine-read by release-promoter)"

# Promoter's INSTANCE_ID_RE: non-empty, no whitespace / ':' / '@'.
# \Z (not $) because Python's $ matches before a trailing newline, but JS's $ (no
# multiline flag) does not — \Z gives the exact-JS semantics the F2 mirror requires.
_INSTANCE_ID_RE = re.compile(r"^[^\s:@]+\Z")
# Promoter's wave-key regex: non-negative integer string.
# Same reasoning: \Z instead of $ to reject keys like "1\n".
_WAVE_KEY_RE = re.compile(r"^(0|[1-9]\d*)\Z")
# Promoter's JSON_FENCE_RE.
_JSON_FENCE_RE = re.compile(r"```json\r?\n(.*?)```", re.DOTALL)
# Chars forbidden in an instanceId (the complement of INSTANCE_ID_RE's class).
_UNSAFE_RE = re.compile(r"[\s:@]")


def _machine_safe(app: str) -> str:
    """Coerce an app/chart name into the promoter's instanceId charset (no ws/:/@).
    Real kbc-stacks chart names are already safe; this is a defensive guarantee so a
    stray char can never produce a manifest the promoter would reject (F1)."""
    return _UNSAFE_RE.sub("-", app)


def compute_instance_id(app: str, source_sha: Optional[str], image_tag: str) -> str:
    """Machine-safe, DETERMINISTIC id. '<app>-<sha[:12]>' when a real source SHA is
    available; otherwise '<app>-<sha256(app\\0image_tag)[:12]>' — NOT a random UUID — so a
    re-run of the same (app, image_tag) yields the SAME id and HIU's idempotency guard still
    detects a duplicate fan-out when pipeline metadata is absent (the test harness passes no
    METADATA, F6). With a real SHA the id identifies the source commit (two fan-outs of
    different image tags built from the same commit share one instanceId — intended). On the
    hash-fallback path: a different tag → a different id; the promoter's duplicate-instanceId
    guard is the backstop for a genuine same-tag collision."""
    safe = _machine_safe(app)
    sha = (source_sha or "").strip()
    if sha and sha.lower() != "unknown":
        return f"{safe}-{_machine_safe(sha[:12])}"
    digest = hashlib.sha256(f"{app}\0{image_tag}".encode()).hexdigest()[:12]
    return f"{safe}-{digest}"


def build_manifest(
    *,
    app: str,
    instance_id: str,
    display_name: str,
    waves: Dict[int, int],
    source_sha: Optional[str] = None,
    source_pr: Optional[str] = None,
) -> Dict[str, Any]:
    """Build the v1 manifest object. `waves` maps wave number -> PR number."""
    manifest: Dict[str, Any] = {
        "manifestVersion": "v1",
        "instanceId": instance_id,
        "displayName": display_name,
        "app": app,
        "anchorWave": 0,
        "waves": {str(w): n for w, n in sorted(waves.items())},
    }
    if source_sha:
        manifest["sourceSha"] = source_sha
    if source_pr:
        manifest["sourcePr"] = source_pr
    return manifest


def build_manual_manifest(
    *,
    app: str,
    instance_id: str,
    display_name: str,
    members,
    source_sha: Optional[str] = None,
    source_pr: Optional[str] = None,
) -> Dict[str, Any]:
    """Build the manual-per-stack (ST-4157) v1 manifest: a flat member set, NO waves.
    `members` are the member PR numbers (sorted for determinism; anchor = min)."""
    manifest: Dict[str, Any] = {
        "manifestVersion": "v1",
        "mode": "manual-per-stack",
        "instanceId": instance_id,
        "displayName": display_name,
        "app": app,
        "members": sorted(members),
    }
    if source_sha:
        manifest["sourceSha"] = source_sha
    if source_pr:
        manifest["sourcePr"] = source_pr
    return manifest


def manifest_block(manifest: Dict[str, Any]) -> str:
    """Render the markdown block (heading + ```json fence) to append to the wave-0
    PR body. The fence shape matches the promoter's extractManifest regex."""
    body = json.dumps(manifest, indent=2, ensure_ascii=False)
    return f"{MANIFEST_HEADING}\n\n```json\n{body}\n```"


def _is_valid_members(v: Any) -> bool:
    """members (manual-per-stack): non-empty list of distinct positive ints (PR numbers).
    Mirrors the promoter's isValidMembers (manifest.ts)."""
    if not isinstance(v, list) or not v:
        return False
    seen = set()
    for m in v:
        # bool is an int subclass — exclude it explicitly.
        if not isinstance(m, int) or isinstance(m, bool) or m <= 0 or m in seen:
            return False
        seen.add(m)
    return True


def is_manifest_v1(x: Any) -> bool:
    """Full mirror of release-promoter isManifestV1 (manifest.ts) so HIU validates EXACTLY
    what the promoter would accept (F2). Never throws."""
    if not isinstance(x, dict):
        return False
    if x.get("manifestVersion") != "v1":
        return False
    iid = x.get("instanceId")
    if not isinstance(iid, str) or not iid or not _INSTANCE_ID_RE.match(iid):
        return False
    if not isinstance(x.get("displayName"), str):
        return False
    app = x.get("app")
    if not isinstance(app, str) or not app:
        return False
    for opt in ("sourceSha", "sourcePr"):
        if opt in x and not isinstance(x[opt], str):
            return False
    # mode discriminator (ST-4157): "manual-per-stack" ⇒ flat members set, NO waves.
    # A manual manifest carrying wave fields is a contradictory shape → reject.
    mode = x.get("mode")
    if mode == "manual-per-stack":
        if "waves" in x or "anchorWave" in x:
            return False
        return _is_valid_members(x.get("members"))
    # Mirror the promoter's `hasOwnProperty('mode') && mode !== undefined` reject (manifest.ts):
    # JSON has no `undefined`, so ANY present `mode` key (incl. null) that isn't
    # "manual-per-stack" is rejected — only an ABSENT mode key is the wave variant.
    if "mode" in x:
        return False
    # wave-ordered manifest (no mode).
    if x.get("anchorWave") != 0:
        return False
    waves = x.get("waves")
    if not isinstance(waves, dict) or not waves or "0" not in waves:
        return False
    seen = set()
    for key, val in waves.items():
        if not _WAVE_KEY_RE.match(key):
            return False
        # bool is an int subclass — exclude it explicitly.
        if not isinstance(val, int) or isinstance(val, bool) or val <= 0 or val in seen:
            return False
        seen.add(val)
    return True


def extract_instance_id(body: Optional[str]) -> Optional[str]:
    """Return the instanceId of the first FULLY-VALID v1 manifest in `body`, else None.
    Used by HIU's idempotency guard to detect a duplicate open release."""
    if not body:
        return None
    for m in _JSON_FENCE_RE.finditer(body):
        try:
            parsed = json.loads(m.group(1))
        except (ValueError, TypeError):
            continue
        if is_manifest_v1(parsed):
            return parsed["instanceId"]
    return None
