# Orbit Kit — one wrapper, any product

The Orbit *wrapper* is product-agnostic. The same machinery — the sidebar
extension, the OTA updater, the rollback registry, the builder, the patch-module
engine — runs Claude Code Orbit, Codex Orbit, or any future "patch a VS Code
extension" product. **Only the patch content and a dozen identity values differ.**

This file is the contract for that. Fork = copy the kit, edit **one config file**,
drop in your patcher + catalog + logos, build. Nothing else is product-specific.

---

## The swap surface is ONE file: `orbit.config.json`

Every product-specific value in the wrapper derives from these canonical fields.
Edit them, and the OTA URLs, globalState keys, view/command ids, user-agent, icon
path, vsix name, and patcher archive glob all follow.

| field         | Claude Code Orbit (this repo)                         | Codex Orbit (what you change it to)         |
|---------------|-------------------------------------------------------|---------------------------------------------|
| `target`      | `anthropic.claude-code`                               | `openai.chatgpt`                            |
| `repo`        | `Lunarwerx/claude-code-orbit`                         | `Lunarwerx/codex-orbit`                     |
| `slug`        | `claude-code-orbit`                                   | `codex-orbit`                               |
| `ns`          | `claudeCodeOrbit`                                     | `codexOrbit`                                |
| `displayName` | `Claude Code Orbit`                                   | `Codex Orbit`                               |
| `publisher`   | `LunarWerx`                                           | `LunarWerx` (same)                          |
| `productNoun` | `Claude Code`                                         | `Codex`                                     |
| `description` | `Power-up for Claude Code: …`                         | `Power-up for Codex: …`                     |
| `keywords`    | `["claude","claude-code","anthropic",…]`              | `["codex","openai","chatgpt",…]`            |
| `wrapperDir`  | `Claude Code Orbit`                                   | `Codex Orbit`                               |
| `logo`        | `claude-code-orbit.png`                               | `codex-orbit.png`                           |
| `patcher.dir` / `.entry` / `.archivePrefix` | `Claude Code` / `patch_claude_vsix_v147.py` / `patch_claude-` | `stable` / `patch_codex.py` / `patch_codex-` |

Run `python tools/orbit_config.py show` to see every derived value, and
`python tools/orbit_config.py check` to confirm the live code still matches the
config (the drift detector — green means the config is faithful).

---

## What's product-specific (you bring these)

1. **`orbit.config.json`** — the table above.
2. **The patcher** — at `{patcher.dir}/{patcher.entry}` (the script that edits the
   stock extension). Yours, not Claude's.
3. **`patch_modules/catalog.json`** — your product's patch modules (same schema,
   different patches). See `patch_modules/catalog.json` for the shape and
   `python tools/patches.py list` for the registry tooling.
4. **`media/`** — your logos: `media/{logo}` (the extension icon) plus the
   recommendation icons (`rec-*.png`).

## What's shared (copy verbatim, never fork)

- `build.py` — the one and only builder.
- `tools/` — `orbit_config.py` (identity), `patches.py` (module registry),
  `archive_patcher.py` (rollback snapshots).
- `{wrapperDir}/extension.js` + `{wrapperDir}/package.json` — the wrapper. Identity
  values come from `orbit.config.json` (see wiring status below).
- The OTA + rollback design (`latest/`, `patchers/`, `*_version.txt`,
  `certified_*.txt`) — these are **generated per product**, not copied. `build.py`
  writes them; the patcher gets archived into `patchers/` automatically.

---

## Fork in five steps (the workflow you asked for)

```
1. Copy this kit folder into your product repo.
2. Edit orbit.config.json    → the 12 fields above.
3. Drop in your patcher       → {patcher.dir}/{patcher.entry}
   + your patch_modules/catalog.json + your media/ logos.
4. python tools/orbit_config.py check   → all green = identity wired correctly.
5. python build.py            → builds latest/{slug}.vsix, snapshots the patcher
                                into the rollback registry, points OTA at {repo}.
```

That's the whole thing. The wrapper code never changes between products — only
the config and the patches do.

---

## Wiring status (be honest about where this is)

**Today:** `orbit.config.json` is the source of truth and the spec. The wrapper
code (`extension.js`, `package.json`, `build.py`) still holds the values inline;
the mapping table below ties each canonical field to the exact lines it controls,
so a fork today means "change the mapped lines" and `orbit_config.py check` proves
you got them all.

**Next increment (owned, in progress):** route `build.py` + a package.json/
extension.js *stamping* step to READ `orbit.config.json`, verified to produce
byte-identical output for Claude Code Orbit so existing users never regress. After
that, a fork is literally "edit orbit.config.json, run build.py" — the mapped
lines stop existing as hand-edits.

### Mapping table — every product-specific location → its config field

| file | location | controlled by |
|------|----------|---------------|
| `{wrapperDir}/extension.js` | `STOCK_ID` | `target` |
| | `OTA_BASE` | `repo` |
| | `OTA_PATCHER_URL` suffix | `patcher.dir` + `patcher.entry` |
| | `OTA_WRAPPER_VSIX_URL` suffix | `slug` |
| | `GS_*` keys, view/container/command ids, `getConfiguration(...)` | `ns` |
| | window title, tooltip, notification text | `displayName` |
| | idle-hint copy ("downloads `anthropic.claude-code`…") | `target` + `productNoun` |
| | logo `joinPath(..,"media","…png")` | `logo` |
| | `User-Agent` header | `slug` |
| `{wrapperDir}/package.json` | `name` | `slug` |
| | `displayName`, `configuration.title`, view `name`/`title` | `displayName` |
| | `description` / `keywords` | `description` / `keywords` |
| | `publisher` | `publisher` |
| | `icon`, viewsContainer `icon`, view `icon` | `logo` |
| | `repository`/`bugs`/`homepage` | `repo` |
| | `{ns}.devMode` setting, viewsContainer `id`, views key + view `id` | `ns` |
| `build.py` | `EXT_NAME` | `slug` |
| | `WRAPPER_DIR` | `wrapperDir` |
| | `VSIX_MANIFEST` Icon path, `INCLUDED_PATHS` logo | `logo` |
| | README logo path rewrite | `wrapperDir` |
| | `patchers/patch_*-*.py` glob | `patcher.archivePrefix` |
| _(drop-ins)_ | `{patcher.dir}/{patcher.entry}` | the patcher itself |
| | `patch_modules/catalog.json` | your patch modules |
| | `media/*.png` | your logos |
