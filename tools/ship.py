#!/usr/bin/env python
"""
ship.py — one command to cut an Orbit release, so the moving parts are never
hand-fed again.

    python tools/ship.py            # experimental (DEFAULT)
    python tools/ship.py --stable   # stable (ONLY when Jacob says "stable")

What it does:
  1. PULLS THE NEWEST Claude Code from the Marketplace and runs the patcher
     against it (the patcher fetches latest when --version is omitted). Aborts
     unless the run prints "Verification passed". So we ALWAYS certify against
     whatever Claude Code is newest at ship time — you never tell it a version.
  2. node --check the patched webview (real JS syntax check; the patcher silently
     skips it when node isn't on PATH, so we make sure node is found).
  3. certified_claude.txt  <- that newest Claude version (auto).
  4. release_channel.txt    <- "experimental" by DEFAULT, "stable" only with
     --stable. THE TAG IS INFALLIBLE: every release is tagged, default
     experimental, stable only when explicitly told.
  5. python build.py        -> archives the patcher into the rollback registry
     (stamped with this channel) and builds the wrapper VSIX.
  6. Prints the exact `git add/commit/push` for the release (the caller runs it,
     then confirms the raw CDN is serving the new CODE before calling it live).

This script never pushes by itself — it does the error-prone prep so a release
is just: run this, then commit + push the printed file list.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PATCHER = ROOT / "Claude Code" / "patch_claude_vsix_v147.py"
CERTIFIED = ROOT / "certified_claude.txt"
CHANNEL = ROOT / "release_channel.txt"


def ensure_node_on_path() -> str | None:
    """The patcher only runs its node --check when `node` resolves on PATH."""
    found = shutil.which("node")
    if found:
        return found
    # Known install location on this machine (node isn't on PATH here).
    cand = Path(os.path.expanduser("~/AppData/Local/Programs/nodejs/node.exe"))
    if cand.exists():
        os.environ["PATH"] = str(cand.parent) + os.pathsep + os.environ.get("PATH", "")
        return str(cand)
    return None


def main() -> int:
    stable = "--stable" in sys.argv[1:]
    channel = "stable" if stable else "experimental"

    node = ensure_node_on_path()
    if not node:
        print("WARNING: node not found — the patcher will SKIP its JS syntax check. "
              "Install node or fix the path before trusting this release.", file=sys.stderr)

    with tempfile.TemporaryDirectory(prefix="orbit-ship-") as tmp:
        out = Path(tmp) / "out.vsix"
        # No --version => the patcher pulls the NEWEST Claude Code from the Marketplace.
        cmd = [sys.executable, str(PATCHER), "anthropic.claude-code",
               "--out", str(out), "--download-dir", tmp]
        print(f"[ship] Pulling newest Claude Code + patching ({channel})...")
        proc = subprocess.run(cmd, capture_output=True, text=True)
        log = proc.stdout + proc.stderr
        print(log)

        if "Verification passed" not in log:
            print("[ship] ABORT: patcher did not report 'Verification passed'. "
                  "Nothing changed.", file=sys.stderr)
            return 1

        m = re.search(r"Downloading anthropic\.claude-code (\d+\.\d+\.\d+)", log)
        if not m:
            print("[ship] ABORT: could not read the newest Claude Code version from "
                  "the patcher output. Nothing changed.", file=sys.stderr)
            return 1
        newest = m.group(1)

        # Belt-and-suspenders node --check on the actual patched webview.
        if node:
            import zipfile
            co = Path(tmp) / "co"
            with zipfile.ZipFile(out) as z:
                z.extract("extension/webview/index.js", co)
            chk = subprocess.run([node, "--check", str(co / "extension" / "webview" / "index.js")],
                                 capture_output=True, text=True)
            if chk.returncode != 0:
                print("[ship] ABORT: node --check failed on the patched webview:\n"
                      + chk.stderr, file=sys.stderr)
                return 1
            print("[ship] node --check: clean")

    CERTIFIED.write_text(newest, encoding="utf-8")
    CHANNEL.write_text(channel, encoding="utf-8")
    print(f"[ship] certified_claude.txt = {newest}")
    print(f"[ship] release_channel.txt  = {channel}")

    print("[ship] Building (archive patcher + wrapper VSIX)...")
    build = subprocess.run([sys.executable, str(ROOT / "build.py")],
                           capture_output=True, text=True)
    print(build.stdout + build.stderr)
    if build.returncode != 0:
        print("[ship] ABORT: build.py failed.", file=sys.stderr)
        return 1

    pv = (ROOT / "patcher_version.txt").read_text(encoding="utf-8").strip()
    wv = (ROOT / "wrapper_version.txt").read_text(encoding="utf-8").strip()
    print("\n" + "=" * 60)
    print(f"  READY TO PUSH — patcher v{pv}, wrapper v{wv}, certified Claude {newest}, channel {channel.upper()}")
    print("=" * 60)
    print("  Commit + push (then confirm the CDN serves the new CODE):")
    print("    git add \"Claude Code/patch_claude_vsix_v147.py\" patcher_version.txt \\")
    print("            certified_claude.txt release_channel.txt patchers/ \\")
    print("            \"Claude Code Orbit/\" wrapper_version.txt wrapper_build.txt \\")
    print("            latest/claude-code-orbit.vsix builds/BUILD_LOG.md builds/*.vsix")
    print("    git commit ; git push orbit main")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
