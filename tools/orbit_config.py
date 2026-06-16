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


def replacement_pairs(src_cfg: dict, dst_cfg: dict) -> list:
    """Ordered (old -> new) identity substitutions to turn the SRC product's
    wrapper text into the DST product's. Longest source first so a shorter token
    (e.g. slug `claude-code-orbit`) never corrupts a longer one that contains it
    (e.g. repo `Lunarwerx/claude-code-orbit`)."""
    sd, dd = derive(src_cfg), derive(dst_cfg)
    src_patch_suffix = sd["otaPatcherUrl"][len(sd["otaBase"]):]
    dst_patch_suffix = dd["otaPatcherUrl"][len(dd["otaBase"]):]
    raw = [
        (src_cfg["repo"], dst_cfg["repo"]),                 # Lunarwerx/<slug>  (before slug)
        (src_patch_suffix, dst_patch_suffix),               # /<dir>/<entry>
        (src_cfg["logo"], dst_cfg["logo"]),                 # <slug>.png
        (src_cfg["target"], dst_cfg["target"]),             # anthropic.claude-code -> openai.chatgpt
        (src_cfg["displayName"], dst_cfg["displayName"]),   # Claude Code Orbit -> Codex Orbit (before productNoun)
        (src_cfg["slug"], dst_cfg["slug"]),                 # claude-code-orbit -> codex-orbit
        (src_cfg["ns"], dst_cfg["ns"]),                     # claudeCodeOrbit -> codexOrbit
        (src_cfg["productNoun"], dst_cfg["productNoun"]),   # Claude Code -> Codex
    ]
    seen, pairs = set(), []
    for a, b in raw:
        if a and a != b and a not in seen:
            seen.add(a)
            pairs.append((a, b))
    pairs.sort(key=lambda p: len(p[0]), reverse=True)
    return pairs


def stamp_text(text: str, pairs: list) -> str:
    for old, new in pairs:
        text = text.replace(old, new)
    return text


def cmd_stamp(args) -> int:
    """Rebrand THIS product's wrapper text into another product's identity.

    Reads the live extension.js + package.json for the SRC (this repo's config)
    and writes identity-stamped copies for the DST config to --out. This is the
    'one source, N products' engine: Codex Orbit's build pulls our wrapper and
    runs this with its own orbit.config.json to mint codex-orbit's wrapper —
    nothing forked, nothing stored. Stamping SRC->SRC is a no-op (byte-identical),
    which is the regression guard.
    """
    src_cfg = load()
    dst_cfg = {k: v for k, v in json.loads(Path(args.to).read_text(encoding="utf-8")).items() if not k.startswith("_")}
    pairs = replacement_pairs(src_cfg, dst_cfg)
    src_wrap = ROOT / src_cfg["wrapperDir"]
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for fname in ("extension.js", "package.json"):
        # Byte-preserving: decode/encode utf-8 WITHOUT newline translation so a
        # self-stamp is byte-identical and CRLF/LF is never silently rewritten.
        text = (src_wrap / fname).read_bytes().decode("utf-8")
        (out / fname).write_bytes(stamp_text(text, pairs).encode("utf-8"))
    print(f"Stamped {src_cfg['slug']} -> {dst_cfg['slug']} into {out}")
    print("  substitutions (old -> new), longest-first:")
    for a, b in pairs:
        print(f"    {a!r} -> {b!r}")
    # Honesty: surface any residual source-product mentions the structural stamp
    # does NOT cover (deep product-isms in copy/filenames that need neutralizing
    # in the SOURCE for true parity).
    resid = []
    noun = src_cfg["productNoun"].split()[0].lower()  # "claude"
    for fname in ("extension.js", "package.json"):
        t = (out / fname).read_text(encoding="utf-8").lower()
        resid.append((fname, t.count(noun)))
    print(f"  residual '{noun}' mentions left (need source-side neutralizing for full parity): "
          + ", ".join(f"{f}={n}" for f, n in resid))
    return 0


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Orbit wrapper config tool")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("show", help="print canonical + derived values").set_defaults(func=cmd_show)
    sub.add_parser("check", help="verify live files match the config").set_defaults(func=cmd_check)
    sp = sub.add_parser("stamp", help="rebrand this wrapper into another product's identity")
    sp.add_argument("--to", required=True, help="path to the DST product's orbit.config.json")
    sp.add_argument("--out", required=True, help="output dir for stamped extension.js + package.json")
    sp.set_defaults(func=cmd_stamp)
    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
