# Production Stable Patch Bundle

DO NOT MODIFY THIS FOLDER unless Jacob explicitly says to promote a new stable
production patcher.

This folder is the locked, known-working fallback that ships inside the Claude
Code Orbit VSIX. Normal patcher work happens elsewhere and can be pulled by OTA
from GitHub. A new VSIX build must not automatically promote that work into this
folder.

Experimental is allowed to move quickly. Stable is not. Do not update this
folder because a new experimental patcher exists, because a new Claude Code
version shipped, or because a local VSIX was rebuilt. Stable changes only when
Jacob explicitly approves a production promotion.

Only promote this folder after the exact patcher and Claude Code version have
been tested together and intentionally approved for production.

Files:

- `patch_claude.py` - the production patcher shipped in the VSIX.
- `stable_version.txt` - the Claude Code Marketplace version proven with this
  patcher.
- `patcher_version.txt` - the production patcher version stamped into patched
  Claude Code as `ccPatchBuildVersion`.

Promotion rule:

1. Test the working patcher against the target Claude Code version.
2. Confirm `node --check` and all patcher verification checks pass.
3. Get explicit human approval to promote stable.
4. Copy the approved patcher/version files into this folder.
5. Build the VSIX.
6. Commit and push the wrapper update path (`wrapper_version.txt` and
   `latest/claude-code-orbit.vsix`) so users can install the stable promotion
   from Orbit instead of manually reinstalling a local VSIX.
