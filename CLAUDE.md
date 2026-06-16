# Claude Code Orbit — workspace instructions

## Releases default to EXPERIMENTAL (Jacob's standing rule)

Every release you push from this repo is **experimental by default**. Unless
Jacob says the word **"stable"** for that specific release, you push it as
experimental — no exceptions, and you don't need to ask which channel.

Mechanically, the tag now **ships inside the patcher itself** (as of patcher
1.2.86 / wrapper 1.2.7) — it is no longer a separate file the UI infers from. The
single source of truth is the **`ORBIT_CHANNEL`** constant in
`Claude Code/patch_claude_vsix_v147.py`. `ship.py` stamps it per release; the
patcher embeds it into the patched webview as `ccPatchChannel`; `archive_patcher`
mirrors it into each manifest entry. So the wrapper reads the **installed** tag
straight from the patched webview, and the **list/banner** tags from the
patcher-sourced manifest — nothing is defaulted. (`release_channel.txt` is still
written for back-compat with already-installed wrappers, but it is no longer the
source of truth.) Red EXPERIMENTAL = "maybe I won't update"; green STABLE = "cool,
I'll update."

On **every** push (all handled by `ship.py`):
- **Default:** `ship.py` stamps `ORBIT_CHANNEL = "experimental"` into the patcher
  (and writes `release_channel.txt` = `experimental` for back-compat). The
  updater tags the available update red.
- **Only when Jacob explicitly says "stable"** for that release: `ship.py --stable`
  stamps `ORBIT_CHANNEL = "stable"` (green tag).
- The tag reflects the **latest** push. Because each archived patcher carries its
  own embedded tag, older versions keep their real tag forever — no backfills.

This lets Jacob push potentially-broken experimental builds freely without
worrying about other users — they see the red tag and can hold off.

See `RELEASE_RULES.md` for the full build/push playbook, and remember: confirm
the actual **code** (not just the version marker) is live on the raw CDN before
telling anyone an update is ready.

## "Push an update" = `python tools/ship.py` (one command)

When Jacob says **"push an update"**, the mechanics are a single script — never
hand-feed the Claude Code version again:

```
python tools/ship.py            # experimental (default)
python tools/ship.py --stable   # stable — ONLY when Jacob says "stable"
```

It auto-**pulls the newest Claude Code** from the Marketplace, verifies the
patcher against it, sets `certified_claude.txt` to that version, writes
`release_channel.txt` (experimental unless `--stable`), and builds. Then commit +
push the printed file list and confirm the CDN serves the new code.

**Two rails, both infallible:**
- Every release certifies against whatever Claude Code is **newest at ship time** —
  never a hand-typed version. (Jacob only ever ships to support the newest Claude.)
- Every release is **tagged**: default **experimental**; tag **stable** ONLY when
  Jacob explicitly says "stable". The only two tags are experimental and stable.

## Keep the Codex Orbit installer IN SYNC (Jacob's standing rule)

There is a **sibling project, Codex Orbit**, at:

```
C:\Users\jacob\Desktop\Project\Codex Orbit          (repo root)
  └─ Codex Orbit-\                                   (the wrapper extension: extension.js, package.json, media)
GitHub remote: https://github.com/Lunarwerx/codex-orbit.git   (origin)
```

It is the **same VS Code extension installer** as this one, pointed at OpenAI's
**Codex** extension instead of Claude Code. The two share the **installer** — the
wrapper sidebar UI (`extension.js`: Install / Update / Previous-versions / the
**stable–experimental channel tag system**) and the release pipeline
(`ship.py`, `build.py`, `archive_patcher.py`, `patchers/manifest.json` with a
per-version `channel` field, the `ORBIT_CHANNEL`-in-patcher → `ccPatchChannel`
embed). Jacob wants these two installers to **never drift**.

**THE RULE — every time you change the installer here, mirror it to Codex:**
when you push an update to this repo that touches the **installer / channel /
release-pipeline** (not a Claude-only patcher feature), you must also apply the
**comparable** change to Codex Orbit's installer, **build theirs, and ship it**,
so both stay in lockstep. Don't wait to be asked.

**Sync the installer, not the target-specific guts:**
- **DO mirror:** wrapper UI/UX, the stable/experimental tag system (red/green,
  no compatibility/preview cruft — Jacob loves the simplicity: *is this version
  stable? yes → good*), `ship.py`/`--stable`, `archive_patcher` channel-mirroring,
  manifest `channel` field, the `ORBIT_CHANNEL`/`ccPatchChannel` mechanism, the
  Previous-versions registry, back-button/cosmetic fixes.
- **DON'T blindly port:** the **patcher internals** (Claude Code anchors ≠ Codex
  anchors) and **target-specific features** (e.g. YOLO / permission modes are a
  Claude Code concept; Codex has its own approval model). Port the *installer*,
  re-implement target-specifics per the other app.

**Naming/path differences to respect on the Codex side:**
- Codex certifies against the newest **Codex** version (its `stable_version.txt`
  holds the Codex baseline, e.g. `26.5609.30741`) — NOT Claude. Codex had no
  `release_channel.txt`; add the channel mechanism the same way (tag in the
  patcher's `ORBIT_CHANNEL`, mirrored to its manifest).
- Codex patcher files are `patch_codex-<v>.py`; versions are their own line
  (patcher ~0.5.x, wrapper ~1.2.x). Follow Codex's own `RELEASE_RULES.md` /
  `AGENTS.md` for its commit + push conventions (remote `origin`, repo `codex-orbit`).
