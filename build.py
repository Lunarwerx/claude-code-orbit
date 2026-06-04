#!/usr/bin/env python
"""
build.py — THE ONE AND ONLY way to build Claude Code Orbit.

    python build.py

There is exactly one build path. Do not invent others (no `npm run build`, no
ad-hoc zipping). Every run:

  * produces a freshly NUMBERED artifact: builds/claude-code-orbit-build-<N>.vsix
    where N strictly increases each build. The highest number is always the
    newest build — that number is your proof a build actually happened.
  * snapshots the current patcher into the remote rollback registry (patchers/)
    via tools/archive_patcher.py. The wrapper VSIX does not bundle patchers or
    a local registry; runtime patching and rollback fetch from the
    authoritative remote.
  * appends a line to builds/BUILD_LOG.md (the visible, version-controlled
    record of every build).
  * copies the new artifact to latest/claude-code-orbit.vsix (the "newest build"
    pointer that gets shipped on push).

INVARIANTS — a plain build NEVER changes a version NUMBER:
  * It does NOT touch Claude Code Orbit/package.json (that bumps only at push,
    only when Jacob says so).
  * It does NOT bump patcher_version.txt or the certified Claude target
    (certified_claude.txt) — it only READS them. That certified target is the one
    human-set value that survives ("the Claude Code version we've verified the
    patcher against"); the archiver stamps each snapshot with it.
  * It DOES write patchers/ (the remote rollback snapshot of the current
    version) so users can roll back to it after the commit is pushed.
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
README_SRC = ROOT / "README.md"
BUILDS_DIR = ROOT / "builds"
LATEST_DIR = ROOT / "latest"
LATEST_VSIX = LATEST_DIR / "claude-code-orbit.vsix"
WRAPPER_VERSION_SRC = ROOT / "wrapper_version.txt"
WRAPPER_BUILD_SRC = ROOT / "wrapper_build.txt"
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
    Path("media/rec-codex-orbit.png"),
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


def _ver_key(v: str):
    return tuple(int(x) if x.isdigit() else 0 for x in str(v).split("."))


def newest_registry_entry():
    """The newest entry in the rollback registry (patchers/manifest.json), or
    None if the registry is missing/empty. The wrapper does not bundle this
    entry; it is recorded so the pushed repo can serve remote rollbacks."""
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

    # Snapshot the current patcher into the rollback registry FIRST, so "newest
    # archived version" below already includes what we're about to ship.
    # Idempotent; non-fatal. Every build saving its version to the registry is
    # what makes "just bump and push, roll back if it breaks" work.
    try:
        sys.path.insert(0, str(ROOT / "tools"))
        import archive_patcher
        archive_info = archive_patcher.archive_current(verbose=False, build_number=build_number)
    except Exception as e:
        raise SystemExit(f"Archive failed: {e}. Refusing to build without updating remote rollback registry.")

    patcher_version = archive_info["version"]
    certified_claude = archive_info["claude"]
    source_label = f"registry patchers/{archive_info['file']}"

    print(f"Bundling Claude Code Orbit -> {out.name}")
    print(f"  patcher registry:  {source_label}")
    print(f"  certified Claude:  {certified_claude}")
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
        # No patcher files or registry are bundled in the wrapper VSIX. Runtime
        # patching and rollback both fetch from the authoritative remote.
        wrapper_build = str(build_number or 0)
        zf.writestr("extension/wrapper_build.txt", wrapper_build)
        files_added.append(f"extension/wrapper_build.txt (#{wrapper_build})")

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
    if build_number is not None:
        WRAPPER_BUILD_SRC.write_text(str(build_number) + "\n", encoding="utf-8")
    print(f"  latest:  {LATEST_VSIX}")
    print(f"  wrapper: {WRAPPER_VERSION_SRC.name} = {manifest['version']}")
    if build_number is not None:
        print(f"  build:   {WRAPPER_BUILD_SRC.name} = {build_number}")

    # Append to the version-controlled build ledger — the visible proof that this
    # build happened and which number is newest.
    if build_number is not None:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = (f"- **Build #{build_number}** — `{out.name}` — "
                f"pkg {manifest['version']}, certified Claude {certified_claude}, "
                f"patcher {patcher_version} — {ts}\n")
        if BUILD_LOG.exists():
            BUILD_LOG.write_text(BUILD_LOG.read_text(encoding="utf-8") + line, encoding="utf-8")
        else:
            BUILD_LOG.write_text(
                "# Build log\n\n"
                "Every `python build.py` appends one line here. "
                "Highest build number = newest build. "
                "The package.json version is NOT changed by a build.\n\n"
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
    print(f"     registry: certified Claude {certified_claude} / patcher {patcher_version}")
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
