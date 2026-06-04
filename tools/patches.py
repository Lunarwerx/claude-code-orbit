#!/usr/bin/env python3
"""Orbit patch-module registry tool.

Reads patch_modules/catalog.json — the single source of truth for which patches
exist — and answers the two questions the modular system is built around:

    python tools/patches.py list                 # what patches exist (by tier)
    python tools/patches.py list --user          # only the user-toggleable ones
    python tools/patches.py resolve --enable usage-meter,yolo-mode
    python tools/patches.py resolve --disable sidebar-panel
    python tools/patches.py check                 # validate the catalog graph

'resolve' answers "if a user picks THIS subset, what actually gets applied?" —
core/required modules are always force-included, dependsOn edges are expanded,
and disabling a module auto-drops everything that depends on it. That dependency
expansion is the "some patches depend on others, work around the exceptions"
contract, made explicit instead of implicit in 2957 lines of Python.

This tool does NOT patch anything yet. It is the registry layer; the apply path
(patch_claude_vsix_v147.py) gets routed through it module-by-module, each step
verified to produce byte-identical output to today's monolith so live users
never see a regression.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "patch_modules" / "catalog.json"


def load() -> tuple[dict, dict]:
    data = json.loads(CATALOG.read_text(encoding="utf-8"))
    by_id = {m["id"]: m for m in data["modules"]}
    return data, by_id


def validate(by_id: dict) -> list[str]:
    """Return a list of problems with the dependency graph (empty == healthy)."""
    problems: list[str] = []
    for mid, m in by_id.items():
        for dep in m.get("dependsOn", []):
            if dep not in by_id:
                problems.append(f"{mid}: dependsOn unknown module '{dep}'")
            elif by_id[dep].get("required"):
                problems.append(
                    f"{mid}: dependsOn '{dep}' is required (always applied) - drop the explicit edge"
                )
    # cycle detection (DFS)
    WHITE, GREY, BLACK = 0, 1, 2
    color = {mid: WHITE for mid in by_id}

    def visit(mid: str, stack: list[str]) -> None:
        color[mid] = GREY
        for dep in by_id[mid].get("dependsOn", []):
            if dep not in by_id:
                continue
            if color[dep] == GREY:
                cyc = " -> ".join(stack[stack.index(dep):] + [dep])
                problems.append(f"dependency cycle: {cyc}")
            elif color[dep] == WHITE:
                visit(dep, stack + [dep])
        color[mid] = BLACK

    for mid in by_id:
        if color[mid] == WHITE:
            visit(mid, [mid])
    return problems


def required_ids(by_id: dict) -> set[str]:
    return {mid for mid, m in by_id.items() if m.get("required")}


def expand_enabled(by_id: dict, enabled: set[str]) -> set[str]:
    """Force-include required modules, then transitively pull in dependsOn."""
    out = set(enabled) | required_ids(by_id)
    changed = True
    while changed:
        changed = False
        for mid in list(out):
            for dep in by_id[mid].get("dependsOn", []):
                if dep in by_id and dep not in out:
                    out.add(dep)
                    changed = True
    return out


def drop_disabled(by_id: dict, disabled: set[str]) -> tuple[set[str], list[str]]:
    """Disable the given modules and cascade to anything that depends on them.

    Required modules cannot be disabled. Returns (final_enabled, notes)."""
    notes: list[str] = []
    kill = set()
    for mid in disabled:
        if mid not in by_id:
            notes.append(f"unknown module '{mid}' ignored")
            continue
        if by_id[mid].get("required"):
            notes.append(f"'{mid}' is required (core scaffold) and cannot be disabled — kept")
            continue
        kill.add(mid)
    # cascade: anyone depending on a killed module is also dropped
    changed = True
    while changed:
        changed = False
        for mid, m in by_id.items():
            if mid in kill or m.get("required"):
                continue
            if any(dep in kill for dep in m.get("dependsOn", [])):
                kill.add(mid)
                changed = True
    final = {mid for mid in by_id if mid not in kill}
    for mid in sorted(kill):
        if mid not in disabled:
            notes.append(f"'{mid}' auto-dropped (depends on a disabled module)")
    return final, notes


def cmd_list(args) -> int:
    data, by_id = load()
    tiers = data["_doc"]["tiers"]
    for tier in tiers:
        mods = [m for m in data["modules"] if m["tier"] == tier]
        if args.user:
            mods = [m for m in mods if m.get("userFacing")]
        if not mods:
            continue
        print(f"\n[{tier}] {tiers[tier]}")
        for m in mods:
            flag = "CORE" if m.get("required") else ("toggle" if m.get("userFacing") else "-")
            dep = f"  deps:{','.join(m['dependsOn'])}" if m.get("dependsOn") else ""
            print(f"  {flag:7} {m['id']:22} {m['title']}{dep}")
    return 0


def cmd_resolve(args) -> int:
    data, by_id = load()
    enable = {s for s in (args.enable or "").split(",") if s}
    disable = {s for s in (args.disable or "").split(",") if s}
    unknown = [s for s in enable | disable if s not in by_id]
    if unknown:
        print(f"unknown module id(s): {', '.join(sorted(unknown))}", file=sys.stderr)
        return 2

    if disable:
        final, notes = drop_disabled(by_id, disable)
    else:
        # default: everything on; --enable narrows to (required + chosen + their deps)
        final = expand_enabled(by_id, enable) if enable else set(by_id)
        notes = []

    print("Will apply:")
    for m in data["modules"]:
        if m["id"] in final:
            tag = "CORE" if m.get("required") else "    "
            print(f"  {tag} {m['id']}")
    skipped = [m["id"] for m in data["modules"] if m["id"] not in final]
    if skipped:
        print("\nSkipped:")
        for mid in skipped:
            print(f"       {mid}")
    for n in notes:
        print(f"  note: {n}")
    print(f"\n{len(final)}/{len(by_id)} modules applied.")
    return 0


def cmd_check(args) -> int:
    _, by_id = load()
    problems = validate(by_id)
    if problems:
        print("Catalog problems:")
        for p in problems:
            print(f"  - {p}")
        return 1
    print(f"Catalog OK: {len(by_id)} modules, graph valid.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Orbit patch-module registry tool")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="list modules by tier")
    p_list.add_argument("--user", action="store_true", help="only user-toggleable modules")
    p_list.set_defaults(func=cmd_list)

    p_res = sub.add_parser("resolve", help="resolve a chosen subset to what actually applies")
    p_res.add_argument("--enable", help="comma-separated module ids to enable (narrows from all)")
    p_res.add_argument("--disable", help="comma-separated module ids to disable (cascades)")
    p_res.set_defaults(func=cmd_resolve)

    p_chk = sub.add_parser("check", help="validate the catalog dependency graph")
    p_chk.set_defaults(func=cmd_check)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
