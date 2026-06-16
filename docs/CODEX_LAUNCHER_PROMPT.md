# Paste this block into Codex Orbit's `AGENTS.md` (its agent instructions)

> This is the exact text to drop into the Codex Orbit workspace so its agent
> knows the launcher is NOT maintained there — it's pulled from Claude Code Orbit
> and rebranded at build. Maintained by Claude Code Orbit; do not edit there.

---

## The launcher is NOT maintained in this repo — pull it, don't write it

This project's **launcher** (the VS Code sidebar extension: install / update /
previous-versions / the stable–experimental channel tags) is **owned by Claude
Code Orbit** and shared verbatim across all Orbit products. We do **not** keep our
own copy. We pull it, rebrand the few identity values into ours, build, and keep
only the finished `.vsix`. **Never hand-edit `extension.js` here.**

**The single source of truth + the "it changed" signal:**
`https://raw.githubusercontent.com/Lunarwerx/claude-code-orbit/main/LAUNCHER_SYNC.md`

**On EVERY release (and any time you're asked to sync the launcher):**

1. **Read** `LAUNCHER_SYNC.md` (URL above). It names the current launcher version
   and carries the changelog. If its `wrapper` version is newer than what we last
   built, the launcher moved and we re-pull.
2. **Pull** these four files from `Lunarwerx/claude-code-orbit` @ `main` into a
   **temp dir** (never commit them here):
   - `Claude%20Code%20Orbit/extension.js`
   - `Claude%20Code%20Orbit/package.json`
   - `tools/orbit_config.py`
   - `build.py`
3. **Rebrand into our identity** — our `orbit.config.json` is the ONLY
   product-specific config. From the pulled checkout, run:
   ```
   python tools/orbit_config.py stamp \
       --to "<this repo>/orbit.config.json" \
       --out "<this repo>/Codex Orbit-"
   ```
   This swaps `target` → `openai.chatgpt`, `repo` → `Lunarwerx/codex-orbit`,
   namespace → `codexOrbit`, plus name/logo/displayName. (Stamping is
   byte-identical when source == target, so it only ever changes identity, never
   behavior.)
4. **Build:** `python build.py` → `latest/codex-orbit.vsix`, using OUR `media/`
   logos and OUR patcher (`stable/patch_codex.py`). Snapshot/manifest/OTA all point
   at `codex-orbit`.
5. **Discard** the pulled launcher source. Commit/keep ONLY the built `.vsix`
   (and our product-specific files below).

**What we own and keep here** (everything else is pulled): `orbit.config.json`,
our patcher `stable/patch_codex.py`, `patch_modules/catalog.json`, and `media/*.png`.

**If the launcher needs a change** (a new button, a UI fix, a channel tweak):
do NOT make it here. Request it upstream in `claude-code-orbit`. It lands there,
`LAUNCHER_SYNC.md` updates, and our next build picks it up automatically. This is
how the two products never drift: one launcher, pulled by all.

**Repo structure to mirror** (so the pulled launcher + `build.py` work unchanged):
`build.py` and `tools/` at repo root; the wrapper in `Codex Orbit-/`;
`orbit.config.json`, `patcher_version.txt`, `wrapper_version.txt`,
`stable_version.txt`, `patchers/manifest.json`, `latest/`, `builds/` laid out
exactly as in `claude-code-orbit`. Run
`python tools/orbit_config.py check` after a sync — all-green means the rebrand is
faithful.
