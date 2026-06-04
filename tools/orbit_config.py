#!/usr/bin/env python3
"""Orbit wrapper config — the one place product identity is derived.

`orbit.config.json` holds ~12 canonical fields. EVERYTHING product-specific in
the wrapper (OTA URLs, globalState keys, view/command ids, user-agent, icon path,
vsix name, patcher archive glob) derives from those fields. This module is the
single derivation function; build.py and the extension-stamping step import it so
there is exactly one source of truth.

    python tools/orbit_config.py show     # print every derived value
    python tools/orbit_config.py check    # assert the live files still match

`check` is the drift detector / byte-parity baseline: it reads the actual
extension.js, package.json, and build.py and confirms they contain exactly the
values this config derives. Green check == the config faithfully describes the
product, so wiring the code to READ the config is a safe, no-op-output refactor.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "orbit.config.json"


def load() -> dict:
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    return {k: v for k, v in cfg.items() if not k.startswith("_")}


def derive(cfg: dict) -> dict:
    repo, slug, ns = cfg["repo"], cfg["slug"], cfg["ns"]
    p = cfg["patcher"]
    ota_base = f"https://raw.githubusercontent.com/{repo}/main"
    return {
        "otaBase": ota_base,
        "otaPatcherUrl": f"{ota_base}/{quote(p['dir'])}/{p['entry']}",
        "otaWrapperVsixUrl": f"{ota_base}/latest/{slug}.vsix",
        "gsRemotePatcherVersion": f"{ns}.remotePatcherVersion",
        "gsLastNotifiedVersion": f"{ns}.lastNotifiedVersion",
        "gsLatestClaudeVersion": f"{ns}.latestClaudeVersion",
        "gsPatcherManifest": f"{ns}.patcherManifest",
        "viewContainerId": ns,
        "viewId": f"{ns}.sidebar",
        "focusCommand": f"{ns}.focusSidebar",
        "configSection": ns,
        "devModeSetting": f"{ns}.devMode",
        "userAgent": f"{slug}-vscode",
        "iconPath": f"media/{cfg['logo']}",
        "latestVsixName": f"{slug}.vsix",
        "extName": slug,
        "patcherArchiveGlob": f"patchers/{p['archivePrefix']}*.py",
    }


def cmd_show(_) -> int:
    cfg = load()
    d = derive(cfg)
    print("== canonical (edit these) ==")
    for k, v in cfg.items():
        print(f"  {k:14} {json.dumps(v)}")
    print("\n== derived (do not edit; computed) ==")
    for k, v in d.items():
        print(f"  {k:24} {v}")
    return 0


def cmd_check(_) -> int:
    cfg = load()
    d = derive(cfg)
    wrapper = ROOT / cfg["wrapperDir"]
    ext_js = (wrapper / "extension.js").read_text(encoding="utf-8")
    pkg = json.loads((wrapper / "package.json").read_text(encoding="utf-8"))
    build_py = (ROOT / "build.py").read_text(encoding="utf-8")

    # OTA urls and the icon path are built by concatenation / joinPath in the
    # live code, so they are not literal substrings — assert the suffixes/parts
    # exactly as the code expresses them.
    patcher_suffix = d["otaPatcherUrl"][len(d["otaBase"]):]   # /Claude%20Code/patch_...py
    vsix_suffix = d["otaWrapperVsixUrl"][len(d["otaBase"]):]   # /latest/<slug>.vsix
    checks = {
        "extension.js: target id":      f'"{cfg["target"]}"' in ext_js,
        "extension.js: OTA base":       d["otaBase"] in ext_js,
        "extension.js: OTA patcher url": patcher_suffix in ext_js,
        "extension.js: OTA vsix url":   vsix_suffix in ext_js,
        "extension.js: globalState ns": d["gsRemotePatcherVersion"] in ext_js,
        "extension.js: view id":        f'"{d["viewId"]}"' in ext_js,
        "extension.js: user-agent":     d["userAgent"] in ext_js,
        "extension.js: logo file":      f'"{cfg["logo"]}"' in ext_js,
        "package.json: name":           pkg.get("name") == cfg["slug"],
        "package.json: displayName":    pkg.get("displayName") == cfg["displayName"],
        "package.json: publisher":      pkg.get("publisher") == cfg["publisher"],
        "package.json: devMode setting": d["devModeSetting"] in (pkg.get("contributes", {}).get("configuration", {}).get("properties", {})),
        "package.json: view id":        any(v.get("id") == d["viewId"] for v in pkg.get("contributes", {}).get("views", {}).get(cfg["ns"], [])),
        "build.py: EXT_NAME":           f'EXT_NAME = "{cfg["slug"]}"' in build_py,
        "build.py: wrapper dir":        f'WRAPPER_DIR = ROOT / "{cfg["wrapperDir"]}"' in build_py,
        "build.py: patcher archive glob": f'"{cfg["patcher"]["archivePrefix"]}' in build_py,
        "patcher entry exists":         (ROOT / cfg["patcher"]["dir"] / cfg["patcher"]["entry"]).exists(),
        "logo exists":                  (wrapper / "media" / cfg["logo"]).exists(),
    }
    bad = [name for name, ok in checks.items() if not ok]
    width = max(len(n) for n in checks)
    for name, ok in checks.items():
        print(f"  [{'OK' if ok else 'DRIFT'}] {name.ljust(width)}")
    if bad:
        print(f"\n{len(bad)} drift(s) - config no longer matches the live files:")
        for b in bad:
            print(f"  - {b}")
        return 1
    print(f"\nAll {len(checks)} grounded - config faithfully describes the product.")
    return 0


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Orbit wrapper config tool")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("show", help="print canonical + derived values").set_defaults(func=cmd_show)
    sub.add_parser("check", help="verify live files match the config").set_defaults(func=cmd_check)
    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
