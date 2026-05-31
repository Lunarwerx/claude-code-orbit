#!/usr/bin/env python
"""
build.py — THE ONE AND ONLY way to build Claude Code Orbit.

    python build.py

There is exactly one build path. Do not invent others (no `npm run build`, no
ad-hoc zipping). Every run:

  * produces a freshly NUMBERED artifact: builds/claude-code-orbit-build-<N>.vsix
    where N strictly increases each build. The highest number is always the
    newest build — that number is your proof a build actually happened.
  * snapshots the current experimental patcher into the rollback registry
    (patchers/) via tools/archive_patcher.py, then bundles the NEWEST registry
    entry as the known-good. There is no separately-promoted "stable" patcher
    anymore: the newest archived version IS the floor; older ones are the
    rollback ladder ("Use previous version"). Falls back to the legacy stable/
    folder only if the registry is unavailable.
  * appends a line to builds/BUILD_LOG.md (the visible, version-controlled
    record of every build).
  * copies the new artifact to latest/claude-code-orbit.vsix (the "newest build"
    pointer that gets shipped on push).

INVARIANTS — a plain build NEVER changes a version NUMBER:
  * It does NOT touch Claude Code Orbit/package.json (that bumps only at push,
    only when Jacob says so).
  * It does NOT bump patcher_version.txt or the Claude target stable_version.txt
    — it only READS them. The one human-set value that survives is the Claude
    target (stable_version.txt = "the Claude Code version we've verified the
    patcher against"); the archiver stamps each snapshot with it.
  * It DOES write patchers/ (the snapshot of the current version) — that's the
    whole point: every build saves the version so users can roll back to it.
The build NUMBER is independent of the package.json version and increments on
its own.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WRAPPER_DIR = ROOT / "Claude Code Orbit"
STABLE_DIR = ROOT / "stable"
PATCHER_SRC = STABLE_DIR / "patch_claude.py"
STABLE_VERSION_SRC = STABLE_DIR / "stable_version.txt"
STABLE_PATCHER_VERSION_SRC = STABLE_DIR / "patcher_version.txt"
STABLE_README_SRC = STABLE_DIR / "README.md"
README_SRC = ROOT / "README.md"
BUILDS_DIR = ROOT / "builds"
LATEST_DIR = ROOT / "latest"
LATEST_VSIX = LATEST_DIR / "claude-code-orbit.vsix"
WRAPPER_VERSION_SRC = ROOT / "wrapper_version.txt"
BUILD_LOG = BUILDS_DIR / "BUILD_LOG.md"
EXT_NAME = "claude-code-orbit"

VSIX_MANIFEST = """<?xml version="1.0" encoding="utf-8"?>
<PackageManifest Version="2.0.0" xmlns="http://schemas.microsoft.com/developer/vsx-schema/2011">
  <Metadata>
    <Identity Language="en-US" Id="{name}" Version="{version}" Publisher="{publisher}" />
    <DisplayName>{display}</DisplayName>
    <Description xml:space="preserve">{description}</Description>
    <Categories>Other</Categories>
    <GalleryFlags>Public</GalleryFlags>
    <Icon>extension/media/claude-code-orbit.png</Icon>
  </Metadata>
  <Installation>
    <InstallationTarget Id="Microsoft.VisualStudio.Code" Version="[1.94.0,)" />
  </Installation>
  <Dependencies />
  <Assets>
    <Asset Type="Microsoft.VisualStudio.Code.Manifest" Path="extension/package.json" Addressable="true" />
    <Asset Type="Microsoft.VisualStudio.Services.Icons.Default" Path="extension/media/claude-code-orbit.png" Addressable="true" />
    <Asset Type="Microsoft.VisualStudio.Services.Content.Details" Path="extension/README.md" Addressable="true" />
  </Assets>
</PackageManifest>
"""

CONTENT_TYPES = """<?xml version="1.0" encoding="utf-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="json" ContentType="application/json" />
  <Default Extension="js"   ContentType="application/javascript" />
  <Default Extension="py"   ContentType="text/x-python" />
  <Default Extension="png"  ContentType="image/png" />
  <Default Extension="svg"  ContentType="image/svg+xml" />
  <Default Extension="xml"  ContentType="application/xml" />
  <Default Extension="md"   ContentType="text/markdown" />
  <Default Extension="vsixmanifest" ContentType="text/xml" />
</Types>
"""

INCLUDED_PATHS = [
    Path("package.json"),
    Path("extension.js"),
    Path("media/claude-code-orbit.png"),
    Path("media/rec-saydeploy.png"),
    Path("media/rec-copilot-suite.png"),
    Path("media/rec-paramount.png"),
    Path("media/rec-connexions.png"),
]


def next_build_number() -> int:
    BUILDS_DIR.mkdir(exist_ok=True)
    existing = sorted(BUILDS_DIR.glob(f"{EXT_NAME}-*.vsix"))
    n = 0
    for p in existing:
        try:
            num = int(p.stem.rsplit("-", 1)[1])
            n = max(n, num)
        except (ValueError, IndexError):
            continue
    return n + 1


def load_manifest() -> dict:
    return json.loads((WRAPPER_DIR / "package.json").read_text(encoding="utf-8"))


def get_patcher_version(manifest: dict) -> str:
    """Use package.json's version as the patcher version. Single source of
    truth — every Marketplace upload requires a version bump anyway, so the
    patcher version moves with the extension automatically."""
    v = manifest.get("version")
    if not v:
        raise SystemExit("package.json is missing 'version'")
    return v


def read_required_text(path: Path, label: str) -> str:
    if not path.exists():
        raise SystemExit(f"Missing stable {label}: {path}")
    v = path.read_text(encoding="utf-8").strip()
    if not v:
        raise SystemExit(f"Stable {label} is empty: {path}")
    return v


def get_stable_patcher_version() -> str:
    """Read the locked production patcher version. This intentionally does not
    follow package.json; VSIX releases can happen without promoting a new
    stable patcher."""
    return read_required_text(STABLE_PATCHER_VERSION_SRC, "patcher version").split()[0]


def get_stable_version() -> str:
    return read_required_text(STABLE_VERSION_SRC, "Claude version").split()[0]


def _ver_key(v: str):
    return tuple(int(x) if x.isdigit() else 0 for x in str(v).split("."))


def newest_registry_entry():
    """The newest entry in the rollback registry (patchers/manifest.json), or
    None if the registry is missing/empty. This is the 'known-good' Orbit bundles
    — there is no separately-promoted 'stable' anymore: the newest archived
    version IS the floor, and older ones are the rollback ladder. The matching
    patcher file (patchers/<file>) is what gets bundled."""
    manifest_path = ROOT / "patchers" / "manifest.json"
    if not manifest_path.exists():
        return None
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = [p for p in data.get("patchers", []) if p.get("version") and p.get("file") and p.get("claude")]
    if not entries:
        return None
    return max(entries, key=lambda p: _ver_key(p["version"]))


def build(out: Path | None = None) -> Path:
    manifest = load_manifest()
    build_number = None
    if out is None:
        build_number = next_build_number()
        out = BUILDS_DIR / f"{EXT_NAME}-build-{build_number}.vsix"
    if out.exists():
        out.unlink()

    # Snapshot the current experimental patcher into the rollback registry FIRST,
    # so "newest archived version" below already includes what we're about to
    # ship. Idempotent; non-fatal. This REPLACES the old hand-promotion of a
    # patcher into stable/ — there is no separate stable patcher to maintain.
    archive_info = None
    try:
        sys.path.insert(0, str(ROOT / "tools"))
        import archive_patcher
        archive_info = archive_patcher.archive_current(verbose=False, build_number=build_number)
    except Exception as e:
        print(f"  archive:  SKIPPED — {e} (rollback registry not updated)")

    # The bundled known-good = the NEWEST entry in the registry. Fall back to the
    # legacy stable/ folder only if the registry is somehow unavailable, so a
    # build never silently ships nothing.
    entry = newest_registry_entry()
    if entry:
        patcher_src = (ROOT / "patchers" / entry["file"]).resolve()
        patcher_version = entry["version"]
        stable_version = entry["claude"]
        source_label = f"registry patchers/{entry['file']}"
    else:
        patcher_src = PATCHER_SRC
        patcher_version = get_stable_patcher_version()
        stable_version = get_stable_version()
        source_label = f"legacy {PATCHER_SRC.relative_to(ROOT)} (registry empty)"
    if not patcher_src.exists():
        raise SystemExit(f"Bundled patcher not found: {patcher_src}")

    print(f"Bundling Claude Code Orbit -> {out.name}")
    print(f"  patcher src:       {source_label}")
    print(f"  known-good Claude: {stable_version}")
    print(f"  patcher ver:       {patcher_version}")
    files_added = []
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        def add(src: Path, arc: str):
            files_added.append(arc)
            zf.write(src, arc)

        zf.writestr("[Content_Types].xml", CONTENT_TYPES)
        zf.writestr(
            "extension.vsixmanifest",
            VSIX_MANIFEST.format(
                name=manifest["name"],
                version=manifest["version"],
                publisher=manifest["publisher"],
                display=manifest["displayName"],
                description=manifest["description"],
            ),
        )
        for rel in INCLUDED_PATHS:
            src = WRAPPER_DIR / rel
            if not src.exists():
                raise SystemExit(f"Missing wrapper file: {rel}")
            add(src, f"extension/{rel.as_posix()}")
        # Bundle the registry-selected patcher + its derived version pins under
        # the same extension/stable/ paths the wrapper already reads, so the
        # extension needs zero changes — only the SOURCE of "known-good" moved
        # from a hand-promoted folder to the newest registry entry.
        add(patcher_src, "extension/stable/patch_claude.py")
        zf.writestr("extension/stable/stable_version.txt", stable_version + "\n")
        zf.writestr("extension/stable/patcher_version.txt", patcher_version + "\n")
        files_added.append(f"extension/stable/stable_version.txt ({stable_version})")
        files_added.append(f"extension/stable/patcher_version.txt ({patcher_version})")
        if STABLE_README_SRC.exists():
            add(STABLE_README_SRC, "extension/stable/README.md")

        # Bundle the rollback registry (manifest + every archived patcher) so the
        # "Previous versions" picker works the moment Orbit is installed — no push,
        # no network. detectState() reads this bundled copy when the OTA cache is
        # empty, and enablePrevious() installs straight from the bundled .py.
        patchers_dir = ROOT / "patchers"
        if (patchers_dir / "manifest.json").exists():
            add(patchers_dir / "manifest.json", "extension/patchers/manifest.json")
            for pf in sorted(patchers_dir.glob("patch_claude-*.py")):
                add(pf, f"extension/patchers/{pf.name}")

        # patch_version.txt — Orbit reads this at runtime to know which patcher
        # version it ships with, then compares against the version baked into
        # the installed Claude Code's webview/index.js to detect outdated installs.
        # Pinned to package.json's version so the Marketplace version IS the
        # patcher version (one number to bump, not two).
        zf.writestr("extension/patch_version.txt", patcher_version)
        zf.writestr("extension/STABLE_VERSION.txt", stable_version)
        files_added.append(f"extension/patch_version.txt (stable v{patcher_version})")
        files_added.append(f"extension/STABLE_VERSION.txt ({stable_version})")

        # README — copy with path rewrite so the GitHub-relative logo image
        # ("Claude Code Orbit/media/...") resolves inside the VSIX where the
        # README sits alongside the media/ folder ("media/...").
        if README_SRC.exists():
            readme = README_SRC.read_text(encoding="utf-8")
            readme = readme.replace("Claude Code Orbit/media/", "media/")
            zf.writestr("extension/README.md", readme)
            files_added.append("extension/README.md")

    size_kb = out.stat().st_size / 1024
    print(f"  files:   {len(files_added) + 2}")
    for f in files_added:
        print(f"    + {f}")
    print(f"  output:  {out}")
    print(f"  size:    {size_kb:.1f} KB")
    LATEST_DIR.mkdir(exist_ok=True)
    shutil.copyfile(out, LATEST_VSIX)
    WRAPPER_VERSION_SRC.write_text(str(manifest["version"]) + "\n", encoding="utf-8")
    print(f"  latest:  {LATEST_VSIX}")
    print(f"  wrapper: {WRAPPER_VERSION_SRC.name} = {manifest['version']}")

    # Append to the version-controlled build ledger — the visible proof that this
    # build happened and which number is newest.
    if build_number is not None:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = (f"- **Build #{build_number}** — `{out.name}` — "
                f"pkg {manifest['version']}, stable Claude {stable_version}, "
                f"patcher {patcher_version} — {ts}\n")
        if BUILD_LOG.exists():
            BUILD_LOG.write_text(BUILD_LOG.read_text(encoding="utf-8") + line, encoding="utf-8")
        else:
            BUILD_LOG.write_text(
                "# Build log\n\n"
                "Every `python build.py` appends one line here. "
                "Highest build number = newest build. "
                "package.json/stable versions are NOT changed by a build.\n\n"
                + line,
                encoding="utf-8",
            )

    # HARD GUARD: a build must NEVER change package.json's version. We only ever
    # READ it. Re-read from disk and fail loudly if it somehow differs — proof the
    # build can't silently bump the Marketplace number.
    after_version = load_manifest().get("version")
    if after_version != manifest["version"]:
        raise SystemExit(
            f"ABORT: build changed package.json version {manifest['version']} -> {after_version}. "
            "Builds must never touch package.json; only an explicit release bumps it."
        )

    # Report the archive snapshot taken at the top of the build (the thing that
    # makes "just keep bumping the version and pushing" work: every build saves
    # the version to the rollback registry, with no extra command and no
    # file-watcher to babysit).
    if archive_info:
        print(f"  archived: patcher v{archive_info['version']} ({archive_info['_action']}) -> "
              f"patchers/{archive_info['file']}  [registry: {archive_info['_count']} version(s)]")

    bn = f"#{build_number}" if build_number is not None else "(custom --out)"
    print("\n" + "=" * 56)
    print(f"  BUILD {bn} COMPLETE")
    print(f"     file:     {out.name}")
    print(f"     bundled:  stable Claude {stable_version} / patcher {patcher_version}")
    print(f"     pkg ver:  {manifest['version']}  (verified UNCHANGED by build)")
    print("=" * 56)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Build Claude Code Orbit VSIX")
    parser.add_argument("--out", help="Explicit output path (default: builds/claude-code-orbit-N.vsix)")
    parser.add_argument("--clean", action="store_true", help="Wipe builds/ before building")
    args = parser.parse_args()

    if args.clean and BUILDS_DIR.exists():
        for p in BUILDS_DIR.glob(f"{EXT_NAME}-*.vsix"):
            p.unlink()
            print(f"  removed {p.name}")

    out_path = Path(args.out).resolve() if args.out else None
    build(out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
