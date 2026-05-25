#!/usr/bin/env python
"""
build.py — Package the Claude Code Orbit wrapper extension as a numbered VSIX.

Output: builds/claude-code-orbit-N.vsix (auto-incremented N)
Bundles only the wrapper + the latest patch_claude_vsix_v147.py script.
The wrapper itself fetches the stock Claude Code VSIX from the marketplace
at runtime and applies the bundled patcher.
"""
from __future__ import annotations

import argparse
import json
import shutil
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WRAPPER_DIR = ROOT / "Claude Code Orbit"
PATCHER_SRC = ROOT / "Claude Code" / "patch_claude_vsix_v147.py"
README_SRC = ROOT / "README.md"
BUILDS_DIR = ROOT / "builds"
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


def build(out: Path | None = None) -> Path:
    if not PATCHER_SRC.exists():
        raise SystemExit(f"Patcher script not found: {PATCHER_SRC}")
    manifest = load_manifest()
    if out is None:
        n = next_build_number()
        out = BUILDS_DIR / f"{EXT_NAME}-{n}.vsix"
    if out.exists():
        out.unlink()

    print(f"Bundling Claude Code Orbit -> {out.name}")
    print(f"  patcher: {PATCHER_SRC.relative_to(ROOT)}")
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
        add(PATCHER_SRC, "extension/patcher/patch_claude.py")

        # patch_version.txt — Orbit reads this at runtime to know which patcher
        # version it ships with, then compares against the version baked into
        # the installed Claude Code's webview/index.js to detect outdated installs.
        # Pinned to package.json's version so the Marketplace version IS the
        # patcher version (one number to bump, not two).
        patcher_version = get_patcher_version(manifest)
        zf.writestr("extension/patch_version.txt", patcher_version)
        files_added.append(f"extension/patch_version.txt (v{patcher_version})")

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
