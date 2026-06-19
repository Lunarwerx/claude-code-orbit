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

VERSIONING — a build AUTO-ITERATES the wrapper version ONLY when the wrapper code
actually changed (so local rebuilds never collide on a version VS Code would
ignore, but a patcher-only release does NOT mint a cosmetic wrapper version):
  * Before bumping, it diffs the wrapper payload (extension.js / package.json /
    media) byte-for-byte against the last shipped latest/claude-code-orbit.vsix
    (see wrapper_payload_changed). If NOTHING changed, the whole wrapper half is
    SKIPPED — no version bump, no VSIX, no wrapper_version.txt / wrapper_build.txt
    / latest copy / BUILD_LOG line. Only the patcher is archived. This is what
    stops a patcher-only release from producing a PHANTOM "wrapper update" (a
    higher version number over byte-identical code) that the sidebar updater would
    offer, costing the user a pointless restart.
  * When the wrapper DID change, it bumps Claude Code Orbit/package.json's PATCH
    component by 1 (…2000 -> 2001 -> 2002 …) so the VSIX is a version VS Code
    treats as new. The bump happens ONCE, up front; bundling never touches it
    again (guarded at the end). An explicit --out always builds (dev escape hatch).
  * Only the WRAPPER/package version iterates here. It does NOT bump
    patcher_version.txt or the certified Claude target
    (certified_claude.txt) — it only READS them. That certified target is the one
    human-set value that survives ("the Claude Code version we've verified the
    patcher against"); the archiver stamps each snapshot with it.
  * It ALWAYS writes patchers/ (the remote rollback snapshot of the current
    patcher) so users can roll back to it after the commit is pushed — that half
    runs on every build, wrapper change or not.
The build NUMBER (builds/…-build-<N>.vsix, wrapper_build.txt) increments only on a
real wrapper build — it is the wrapper-artifact counter; the package version is the
VS-Code-facing version. Both climb together, one per wrapper change.
"""
from __future__ import annotations

import argparse
import json
import re
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


def wrapper_payload_changed() -> bool:
    """True if the wrapper's installed payload differs from what's already shipped
    in latest/claude-code-orbit.vsix.

    Compares every file the VSIX actually carries (extension.js, package.json, the
    media icons) BYTE-FOR-BYTE against the matching `extension/<path>` entry inside
    the last shipped VSIX. The comparison runs BEFORE the version bump, so the
    package.json being checked still holds the previously-shipped version — meaning
    a "version-only" difference reads as UNCHANGED. Any real edit (a UI change in
    extension.js, a new command in package.json, a new icon) reads as CHANGED.

    Why this gate exists: build.py used to bump the wrapper version on every build,
    even a patcher-only release where extension.js never changed. The sidebar
    updater offers a wrapper update whenever the remote version is higher — so those
    cosmetic bumps produced a PHANTOM "wrapper update" that cost the user a restart
    for byte-identical code, then surfaced the real patcher update right after (the
    "update, restart, oh here's another update" treadmill). Skipping the wrapper
    bump/rebuild when nothing changed kills the phantom at the source.

    Returns True (i.e. "build the wrapper") whenever the latest VSIX is missing or
    unreadable, so uncertainty never silently skips a needed build."""
    if not LATEST_VSIX.exists():
        return True
    try:
        with zipfile.ZipFile(LATEST_VSIX) as z:
            shipped = set(z.namelist())
            for rel in INCLUDED_PATHS:
                src = WRAPPER_DIR / rel
                arc = f"extension/{rel.as_posix()}"
                if not src.exists():
                    return True            # a tracked wrapper file vanished -> changed
                if arc not in shipped:
                    return True            # file not in the last VSIX -> changed
                if z.read(arc) != src.read_bytes():
                    return True            # content differs -> changed
        return False
    except Exception:
        return True                        # unreadable VSIX -> rebuild to be safe


def bump_package_version() -> str:
    """Auto-iterate the wrapper version on EVERY build (owner's standing choice):
    increment the patch component so each VSIX is a version VS Code treats as new
    (…2000 -> 2001 -> 2002 …) — letting the owner rebuild hundreds of times locally
    without the "same version, VS Code won't pick it up" trap. Only the wrapper/
    package version iterates; the patcher version is separate. Regex-replaces just
    the digits so package.json formatting/key-order is preserved."""
    pkg_path = WRAPPER_DIR / "package.json"
    text = pkg_path.read_text(encoding="utf-8")
    m = re.search(r'("version"\s*:\s*")(\d+)\.(\d+)\.(\d+)(")', text)
    if not m:
        raise SystemExit("bump_package_version: no X.Y.Z version found in package.json")
    new_v = f"{m.group(2)}.{m.group(3)}.{int(m.group(4)) + 1}"
    pkg_path.write_text(text[: m.start()] + m.group(1) + new_v + m.group(5) + text[m.end():],
                        encoding="utf-8")
    return new_v


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
    explicit_out = out is not None
    # A release is USUALLY a patcher change. Only bump + re-ship the wrapper when its
    # installed payload (extension.js / package.json / media) actually differs from
    # what's already in latest/claude-code-orbit.vsix — otherwise the version bumps
    # with no functional change and the sidebar updater offers a PHANTOM wrapper
    # update (see wrapper_payload_changed). An explicit --out always builds.
    wrapper_changed = explicit_out or wrapper_payload_changed()
    build_number = next_build_number() if (wrapper_changed and not explicit_out) else None

    # Snapshot the current patcher into the rollback registry on EVERY build (wrapper
    # changed or not) — the registry must always carry the newest patcher. Idempotent;
    # we refuse to proceed without it. Done before the wrapper bump because the
    # archiver reads patcher_version.txt / certified_claude.txt, never package.json.
    try:
        sys.path.insert(0, str(ROOT / "tools"))
        import archive_patcher
        archive_info = archive_patcher.archive_current(verbose=False, build_number=build_number)
    except Exception as e:
        raise SystemExit(f"Archive failed: {e}. Refusing to build without updating remote rollback registry.")

    patcher_version = archive_info["version"]
    certified_claude = archive_info["claude"]
    source_label = f"registry patchers/{archive_info['file']}"

    if not wrapper_changed:
        # Patcher-only release: leave EVERY wrapper artifact untouched — no version
        # bump, no VSIX, no wrapper_version.txt / wrapper_build.txt / latest copy /
        # BUILD_LOG line. This is the fix for the phantom "wrapper update + restart"
        # treadmill: the remote wrapper version only advances when the wrapper code
        # truly changed, so the updater never offers a cosmetic-only update.
        wv = load_manifest().get("version")
        print("Wrapper payload unchanged vs latest/claude-code-orbit.vsix —")
        print("  skipping wrapper rebuild + version bump (no phantom update emitted).")
        print(f"  wrapper stays:     v{wv}  ({WRAPPER_VERSION_SRC.name} untouched)")
        print(f"  archived: patcher v{archive_info['version']} ({archive_info['_action']}) -> "
              f"patchers/{archive_info['file']}  [registry: {archive_info['_count']} version(s)]")
        print("\n" + "=" * 56)
        print("  PATCHER-ONLY RELEASE COMPLETE — wrapper unchanged")
        print(f"     registry: certified Claude {certified_claude} / patcher {patcher_version}")
        print("=" * 56)
        return LATEST_VSIX

    # --- wrapper changed (or explicit --out): full wrapper build ---------------
    # Auto-iterate the wrapper version, so the bundled manifest + every downstream
    # write (VSIX, wrapper_version.txt, BUILD_LOG) carries the new number.
    new_version = bump_package_version()
    print(f"Wrapper version auto-bumped -> {new_version}")
    manifest = load_manifest()
    if out is None:
        out = BUILDS_DIR / f"{EXT_NAME}-build-{build_number}.vsix"
    if out.exists():
        out.unlink()

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

    # GUARD: bundling must not change the version BEYOND the single intentional
    # bump done up front (bump_package_version). Re-read and confirm it still
    # matches what we bundled — proof nothing downstream silently re-bumped it.
    after_version = load_manifest().get("version")
    if after_version != manifest["version"]:
        raise SystemExit(
            f"ABORT: version changed during bundling {manifest['version']} -> {after_version}. "
            "Only the upfront auto-bump may change package.json; bundling must not."
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
    print(f"     pkg ver:  {manifest['version']}  (auto-bumped; bundling verified clean)")
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
