# Claude Code Orbit Release Rules

These are production notes for future agents. Follow them unless Jacob
explicitly says otherwise.

## Version numbers — there are TWO, plus a certified tag

Do not assume any two move together.

1. **Patcher version** — file: `patcher_version.txt`. Series: **1.2.x** (e.g.
   1.2.65, 1.2.66). What it versions: the patch SCRIPT
   (`Claude Code/patch_claude_vsix_v147.py`) that rewrites Claude Code's webview.
   How it ships: over-the-air from the GitHub `orbit` repo — the user gets it by
   clicking **"Check for updates."** Independent of package.json; fine to bump for
   every patcher fix. Each bump is archived into the rollback registry
   (`patchers/`) by `tools/archive_patcher.py`.

2. **Wrapper / VSIX version** — files: `Claude Code Orbit/package.json`
   (`"version"`) → `wrapper_version.txt` (derived by `build.py`).
   Series: **1.1.x** (e.g. 1.1.9). What it versions: the Orbit extension itself
   (the VSIX). This MUST track the VS Code Marketplace line. Bump it ONLY at a
   push/release, and ONLY when Jacob says so.

**Certified Claude tag** — file: `certified_claude.txt`. Anthropic's Claude Code
version the current patcher has been verified against. NOT an Orbit version
number. `archive_patcher.py` stamps it onto each registry entry so a rollback
re-installs that patcher against a Claude version it's known to work with. Update
it when you verify the patcher against a newer Claude Code release.

There is **no "stable" channel.** The newest patcher is what everyone runs; if it
breaks, **Previous versions** rolls back to an earlier registry entry.

## Building — there is exactly ONE way

To build, run:

```
python build.py
```

That is the only build path. Do not create or use any other (no `npm run
build`, no manual zipping, no second script). Every run:

- writes a freshly NUMBERED artifact `builds/claude-code-orbit-build-<N>.vsix`
  where `N` strictly increases. The highest number is always the newest build —
  that number is the proof a build actually happened.
- appends a line to `builds/BUILD_LOG.md` (tracked in git, so the history is
  visible on GitHub). The numbered `build-*.vsix` files are also tracked.
- archives the current patcher into the rollback registry (`patchers/`), bundles
  that registry (newest entry = offline fallback), and copies the result to
  `latest/claude-code-orbit.vsix`.

A plain build NEVER changes a version number:

- It does NOT touch `Claude Code Orbit/package.json`. That version bumps ONLY at
  push/release time, and ONLY when Jacob says so.
- It does NOT touch `patcher_version.txt` or `certified_claude.txt`. It DOES write
  `patchers/` — saving the current version so users can roll back to it.

So: "build" = make the next numbered VSIX. "push" = bump package.json + ship.
They are separate actions.

## Two Update Channels

Orbit has two separate update channels.

1. Experimental patcher updates

Source: GitHub OTA files.

Files:

- `Claude Code/patch_claude_vsix_v147.py`
- `patcher_version.txt`

User flow: the user clicks `Check experimental updates`, sees `Update found`,
and installs from inside Orbit.

Purpose: move quickly when Anthropic releases a new Claude Code version or the
patch anchors need repair.

2. Orbit wrapper updates

Source: GitHub-hosted wrapper artifact.

Files:

- `wrapper_version.txt`
- `latest/claude-code-orbit.vsix`
- `Claude Code Orbit/extension.js`
- `Claude Code Orbit/package.json`

User flow: the user clicks `Check experimental updates`, sees an Orbit wrapper
update, and installs from inside Orbit.

Purpose: update the sidebar/updater/wrapper itself without asking the user to
manually reinstall a local VSIX.

## Required Rule After Every Fix

When making any user-facing fix, commit and push the matching GitHub update path
before calling the work done.

- If the fix changes the patcher, bump `patcher_version.txt`, commit the
  patcher, and push to the GitHub OTA repo.
- If the fix changes the Orbit wrapper/sidebar/updater, bump
  `Claude Code Orbit/package.json`, run `python build.py`, commit
  `wrapper_version.txt` and `latest/claude-code-orbit.vsix`, and push to the
  GitHub OTA repo.
- If both changed, ship both update paths.

The goal is that the user should normally click `Check experimental updates` and
install the new update from Orbit. They should not need to manually install a new
local VSIX after every fix.

## Where Experimental Updates Must Be Pushed

Orbit's live updater does not read from whatever the local Git `origin` happens
to be. The extension code fetches OTA files from:

`https://raw.githubusercontent.com/Lunarwerx/claude-code-orbit/main`

That means experimental releases must be pushed to:

`https://github.com/Lunarwerx/claude-code-orbit.git`

In this workspace, that remote is named `orbit`. `origin` may point at
`Lunarwerx/claude-script`, which is not the repo checked by the Orbit updater.

For patcher-only experimental releases:

1. Edit `Claude Code/patch_claude_vsix_v147.py`.
2. Bump root `patcher_version.txt`.
3. Commit the patcher and version marker.
4. Push to `orbit main`.
5. Optionally push the same commit to `origin main` only to keep this workspace
   mirrored, but do not treat that as the release.

For wrapper/sidebar/updater releases:

1. Edit the wrapper files under `Claude Code Orbit/`.
2. Bump `Claude Code Orbit/package.json`.
3. Run `python build.py`.
4. Commit `wrapper_version.txt` and `latest/claude-code-orbit.vsix`.
5. Push to `orbit main`.

The release is not complete until `Lunarwerx/claude-code-orbit` contains the new
version marker or wrapper artifact.

## Post-Push: Confirm the CDN Before Telling Anyone "It's Live"

`raw.githubusercontent.com` has a CDN cache. After `git push orbit main`
succeeds, the raw URL will keep serving the **old** version for 2–5 minutes.
During that window, Orbit's "Check experimental updates" will correctly report
"no updates" — because the CDN still returns the old version marker.

**Required step before calling a release done:**

1. Push to `orbit main`.
2. Run this and compare the result to the local version file:

   ```
   Invoke-WebRequest -Uri "https://raw.githubusercontent.com/Lunarwerx/claude-code-orbit/main/patcher_version.txt" -UseBasicParsing | Select-Object -ExpandProperty Content
   ```

3. If the CDN still returns the old version: **tell the user explicitly** how
   long it has been since the push and that they need to wait for the CDN cache
   to expire (typically 2–5 minutes). Do not just guess — confirm you actually
   checked the URL and saw it was stale.
4. If the CDN returns the new version: the update is live. Tell the user they
   can click "Check for updates" now.

Never claim an update is available until the raw CDN confirms it is serving the
new version marker.

## Recovery is "Previous versions", not a stable channel

There is no separately-promoted stable build. The newest patcher is what everyone
installs (always patching the newest Claude Code). If a release breaks for
someone, the recovery path is **Previous versions**: install an earlier entry
from the rollback registry (`patchers/manifest.json`).

- Every build/release archives the current patcher into `patchers/` via
  `tools/archive_patcher.py`. The newest entry doubles as the offline fallback
  bundled in the VSIX; the rest are the rollback ladder.
- Each registry entry records the Claude version the patcher was **certified
  against** (`claude` field, sourced from `certified_claude.txt`). A rollback
  re-installs that patcher pinned (via `--version`) to its certified Claude, so
  every previous-version install is a known-good (patcher, Claude) pair — never an
  old patcher fired blind at whatever Claude is installed.
- Update `certified_claude.txt` whenever you verify the patcher against a newer
  Claude Code release, so the next archived entry carries the right pin.

Verify-before-certify (ALWAYS, no exceptions):
`python "Claude Code/patch_claude_vsix_v147.py" anthropic.claude-code --version <newest> --out tmp.vsix --download-dir tmp`
must end with "Verification passed (N checks)" before you set `certified_claude.txt`
to `<newest>`.
