#!/usr/bin/env python
"""
archive_patcher.py — snapshot the CURRENT experimental patcher into the rollback
registry so users can later "Use previous version".

    python tools/archive_patcher.py

Run this once whenever you cut a new experimental patcher version (i.e. after you
bump patcher_version.txt and before/with the push to `orbit`). It is the "save
every version" half of the rollback feature.

What it does (idempotent):
  * reads patcher_version.txt   — the current patcher version (e.g. 1.2.66)
  * reads certified_claude.txt  — the Claude Code version this patcher was
                                  verified against (the certified tag to record)
  * copies Claude Code/patch_claude_vsix_v147.py -> patchers/patch_claude-<ver>.py
  * upserts that version's entry in patchers/manifest.json (newest last)

Why record a Claude pin per patcher: the patcher is NOT version-independent — its
anchors only match a window of Claude Code releases. Rolling back re-installs the
archived patcher pinned to THIS certified Claude version, so a previous-version
install is always a known-good (patcher, Claude) pair — never an old patcher
fired blind at whatever Claude happens to be installed.

This NEVER bumps a version number. It only reads the version pins and snapshots
the current patcher. Re-running on an already-archived version refreshes its
file + entry in place (no duplicate).
"""
from __future__ import annotations

import json
import re
import shutil
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXPERIMENTAL_PATCHER = ROOT / "Claude Code" / "patch_claude_vsix_v147.py"
PATCHER_VERSION_SRC = ROOT / "patcher_version.txt"
CERTIFIED_CLAUDE_SRC = ROOT / "certified_claude.txt"
RELEASE_CHANNEL_SRC = ROOT / "release_channel.txt"
PATCHERS_DIR = ROOT / "patchers"
MANIFEST = PATCHERS_DIR / "manifest.json"
BUILDS_DIR = ROOT / "builds"
EXT_NAME = "claude-code-orbit"


def read_version(path: Path, label: str) -> str:
    if not path.exists():
        raise SystemExit(f"Missing {label}: {path}")
    v = path.read_text(encoding="utf-8").strip().split()[0]
    if not v:
        raise SystemExit(f"{label} is empty: {path}")
    return v


def current_build_number() -> int | None:
    """Highest existing build number in builds/, purely informational."""
    if not BUILDS_DIR.exists():
        return None
    n = None
    for p in BUILDS_DIR.glob(f"{EXT_NAME}-*.vsix"):
        try:
            num = int(p.stem.rsplit("-", 1)[1])
            n = num if n is None else max(n, num)
        except (ValueError, IndexError):
            continue
    return n


def read_patcher_channel() -> "str | None":
    """The channel embedded in the patcher's own ORBIT_CHANNEL constant — the
    single source of truth for the tag. Returns 'experimental'/'stable' or None
    when the marker is somehow absent."""
    try:
        src = EXPERIMENTAL_PATCHER.read_text(encoding="utf-8")
        m = re.search(r'^ORBIT_CHANNEL:\s*str\s*=\s*"([^"]+)"', src, flags=re.M)
        if m:
            c = m.group(1).strip().lower()
            if c in ("experimental", "stable"):
                return c
    except Exception:
        pass
    return None


def read_patcher_whatsnew() -> str:
    """The release notes embedded in the patcher's own ORBIT_WHATSNEW constant
    (JSON-encoded by tools/ship.py). Mirrored into the manifest's per-version
    `whatsNew` field so the wrapper can show the What's-New popup without installing
    anything. Returns "" when the marker is absent or empty."""
    try:
        src = EXPERIMENTAL_PATCHER.read_text(encoding="utf-8")
        m = re.search(r'^ORBIT_WHATSNEW:\s*str\s*=\s*("(?:[^"\\]|\\.)*")\s*$', src, flags=re.M)
        if m:
            val = json.loads(m.group(1))
            if isinstance(val, str):
                return val
    except Exception:
        pass
    return ""


def read_release_channel_file() -> "str | None":
    """Back-compat fallback: the old side-file tag. Only used if the patcher has
    no embedded ORBIT_CHANNEL (it always should, going forward)."""
    try:
        if RELEASE_CHANNEL_SRC.exists():
            c = RELEASE_CHANNEL_SRC.read_text(encoding="utf-8").strip().lower()
            if c in ("stable", "experimental"):
                return c
    except Exception:
        pass
    return None


def load_manifest() -> dict:
    if MANIFEST.exists():
        data = json.loads(MANIFEST.read_text(encoding="utf-8"))
        if not isinstance(data.get("patchers"), list):
            raise SystemExit("manifest.json is malformed: missing 'patchers' list")
        return data
    # First-ever archive — create a fresh registry.
    return {
        "schema": 1,
        "comment": "Registry of rollback-able Orbit patcher versions. Maintained by tools/archive_patcher.py.",
        "patchers": [],
    }


def archive_current(verbose: bool = True, build_number: "int | None" = None) -> dict:
    """Snapshot the current experimental patcher into the registry (idempotent).
    Returns the manifest entry. Raises SystemExit on a hard error (missing
    source / version files). Safe to call repeatedly — re-running on an
    already-archived version refreshes its file + entry in place.

    This is the function build.py calls so a snapshot happens automatically on
    every build/push — no separate command, no file watcher to babysit.
    `build_number` lets the caller pass the build that's about to be written
    (build.py archives BEFORE the .vsix exists); falls back to the highest
    existing build when omitted (the standalone CLI path).
    """
    if not EXPERIMENTAL_PATCHER.exists():
        raise SystemExit(f"Experimental patcher not found: {EXPERIMENTAL_PATCHER}")
    version = read_version(PATCHER_VERSION_SRC, "patcher_version.txt")
    claude = read_version(CERTIFIED_CLAUDE_SRC, "certified_claude.txt")
    # The channel THIS version ships as is read from the patcher ITSELF — its
    # ORBIT_CHANNEL constant is the single source of truth (the tag travels inside
    # the patcher, and is also embedded into the patched webview as ccPatchChannel).
    # The manifest entry below is just a fast-listing MIRROR of that value, never an
    # independent field that can drift. Fall back to the old release_channel.txt,
    # then the standing default (experimental), only if the marker is absent.
    channel = read_patcher_channel() or read_release_channel_file() or "experimental"

    PATCHERS_DIR.mkdir(exist_ok=True)
    dest_name = f"patch_claude-{version}.py"
    dest = PATCHERS_DIR / dest_name
    shutil.copyfile(EXPERIMENTAL_PATCHER, dest)

    manifest = load_manifest()
    entry = {
        "version": version,
        "channel": channel,
        "build": build_number if build_number is not None else current_build_number(),
        "claude": claude,
        "file": dest_name,
        "date": datetime.now().strftime("%Y-%m-%d"),
        # "What's New" notes, mirrored from the patcher's ORBIT_WHATSNEW constant so
        # the wrapper can show them per-version without installing.
        "whatsNew": read_patcher_whatsnew(),
    }

    patchers = manifest["patchers"]
    existing = next((i for i, p in enumerate(patchers) if p.get("version") == version), None)
    if existing is not None:
        # Preserve any human-written notes when refreshing in place.
        notes = patchers[existing].get("notes")
        if notes:
            entry["notes"] = notes
        patchers[existing] = entry
        action = "refreshed"
    else:
        patchers.append(entry)
        action = "added"

    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    if verbose:
        print(f"Archived patcher v{version} ({action}) -> patchers/{dest_name}")
        print(f"  verified Claude pin: {claude}")
        print(f"  build: {entry['build']}")
        print(f"  registry now has {len(patchers)} version(s): "
              + ", ".join(p['version'] for p in patchers))
        print("\nRemember: commit patchers/ and push to `orbit` so the 'Use previous "
              "version' button can fetch it.")
    entry["_action"] = action
    entry["_count"] = len(patchers)
    return entry


def main() -> int:
    archive_current(verbose=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
