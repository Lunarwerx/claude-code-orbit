**Fixes Orbit on the newest Claude Code (2.1.186).**

Claude Code 2.1.186 quietly rebuilt how its sidebar is rendered under the hood. Our patcher didn't recognize the new shape, so anyone who updated to 2.1.186 saw an error when Orbit tried to patch — and the Orbit session pane didn't load. This build re-teaches the patcher the new shape, so the **sessions sidebar is back**: archive / pin / star, per-chat color, the status dots, the "2 days ago" times, the collapsible Starred / Pinned / Sessions / Archived sections, search, and the settings gear all work again on 2.1.186.

**Heads-up (this beta):** the Copilot-style composer bottom bar — the model picker, the standalone effort slider, the steer / queue / stop send-split, and the queued-message bubbles — are temporarily turned off on 2.1.186 while we re-fit them to Claude's new layout. Claude's own native composer (model menu, effort, send) is fully there in the meantime, so nothing is blocked. Those Orbit extras come back in the next beta.

If you're still on Claude Code 2.1.185 or earlier and want the full set today, you can install a previous Orbit version from the gear ▸ Previous versions.
