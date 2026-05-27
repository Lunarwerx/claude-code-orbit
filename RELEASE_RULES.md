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

## Stable Is Different

Stable is the production fallback shipped inside the Orbit VSIX.

- Stable lives in `stable/`.
- Stable is bundled into the wrapper VSIX by `build.py`.
- Stable must not be updated just because experimental changed.
- Stable must not be updated just because Anthropic released a new Claude Code.
- Stable is promoted only after the exact Claude Code version and exact patcher
  have been tested together and Jacob explicitly approves the promotion.

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

