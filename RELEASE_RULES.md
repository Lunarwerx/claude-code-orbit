# Claude Code Orbit Release Rules

These are production notes for future agents. Follow them unless Jacob
explicitly says otherwise.

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
   can click "Check experimental updates" now.

Never claim an update is available until the raw CDN confirms it is serving the
new version marker.

## Stable Is Different

Stable is the production fallback shipped inside the Orbit VSIX.

- Stable lives in `stable/`.
- Stable is bundled into the wrapper VSIX by `build.py`.
- Stable must not be updated just because experimental changed.
- Stable must not be updated just because Anthropic released a new Claude Code.
- Stable is promoted only after the exact Claude Code version and exact patcher
  have been tested together and Jacob explicitly approves the promotion.
- Do not edit `stable/`, `stable_version.txt`, or stable patcher pins unless
  Jacob explicitly asks for a stable promotion.

Expected cadence:

- Experimental can move quickly, often within a day or two of upstream Claude
  Code changes, after the obvious breakage is fixed.
- Stable can lag behind by days or weeks. That is intentional. Stable is the
  known-good recovery path, not the bleeding-edge path.

## Stable Promotion Checklist

Only promote stable when all of these are true:

1. The experimental patcher works against the target Claude Code version.
2. The exact pair has been tested manually.
3. `node --check` and patcher verification pass.
4. Jacob explicitly says to promote stable.
5. Copy the approved patcher and version files into `stable/`.
6. Build a new Orbit VSIX.
7. Commit and push the wrapper update path so users can update through Orbit.
