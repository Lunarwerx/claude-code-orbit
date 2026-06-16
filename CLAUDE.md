# Claude Code Orbit — workspace instructions

## Releases default to EXPERIMENTAL (Jacob's standing rule)

Every release you push from this repo is **experimental by default**. Unless
Jacob says the word **"stable"** for that specific release, you push it as
experimental — no exceptions, and you don't need to ask which channel.

Mechanically, the channel is the one-word file **`release_channel.txt`** at the
repo root (served over OTA). It drives the red/green tag the updater shows on any
available update — **red EXPERIMENTAL** = "maybe I won't update"; **green
STABLE** = "cool, I'll update."

On **every** push:
- **Default:** write `release_channel.txt` = `experimental` and include it in the
  release commit. The updater then tags the available update red.
- **Only when Jacob explicitly says "stable"** for that release: write
  `release_channel.txt` = `stable` (green tag).
- The tag reflects the **latest** push. If you ship experimental builds and then
  a stable one, flip the file to `stable` in that stable push.

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
