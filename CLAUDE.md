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
