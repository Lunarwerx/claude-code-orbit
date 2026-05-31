#!/usr/bin/env python
"""
archive_patcher.py — snapshot the CURRENT experimental patcher into the rollback
registry so users can later "Use previous version".

    python tools/archive_patcher.py

Run this once whenever you cut a new experimental patcher version (i.e. after you
bump patcher_version.txt and before/with the push to `orbit`). It is the "save
every version" half of the rollback feature.

What it does (idempotent):
  * reads patcher_version.txt        — the experimental patcher version (e.g. 1.2.58)
  * reads stable/stable_version.txt  — the verified Claude Code pin to record
  * copies Claude Code/patch_claude_vsix_v147.py -> patchers/patch_claude-<ver>.py
  * upserts that version's entry in patchers/manifest.json (newest last)

Why record a Claude pin per patcher: the patcher is NOT version-independent — its
anchors only match a window of Claude Code releases. Rollback re-installs the
archived patcher pinned to THIS recorded Claude version (same mechanism as "Use
verified build"), so a rollback is always a known-good (patcher, Claude) pair —
never an old patcher fired blind at whatever Claude happens to be installed.

This NEVER bumps a version number and NEVER touches stable/. It only reads the
version pins and snapshots the experimental patcher. Re-running on an
already-archived version refreshes its file + entry in place (no duplicate).
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXPERIMENTAL_PATCHER = ROOT / "Claude Code" / "patch_claude_vsix_v147.py"
PATCHER_VERSION_SRC = ROOT / "patcher_version.txt"
STABLE_VERSION_SRC = ROOT / "stable" / "stable_version.txt"
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
    claude = read_version(STABLE_VERSION_SRC, "stable/stable_version.txt")

    PATCHERS_DIR.mkdir(exist_ok=True)
    dest_name = f"patch_claude-{version}.py"
    dest = PATCHERS_DIR / dest_name
    shutil.copyfile(EXPERIMENTAL_PATCHER, dest)

    manifest = load_manifest()
    entry = {
        "version": version,
        "build": build_number if build_number is not None else current_build_number(),
        "claude": claude,
        "file": dest_name,
        "date": datetime.now().strftime("%Y-%m-%d"),
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
