**This stable release rolls up everything from the recent betas — here's what's new.**

**Auto-continue is now dependable.** Turn it on from the gear menu and Orbit sends "keep going" for you when a chat stops. This release fixes every case where it used to get stuck:
- It resumes the moment your **5-hour or weekly usage limit resets** — previously a limited chat could stay parked even after the limit lifted.
- It **survives a reload or window restart** — a chat that stopped on an error or a usage limit gets picked back up on its own (non-archived chats only, and only from a real stop, never a chat that merely mentioned an error).
- It handles the **tricky double case**: when the server flashes a temporary "rate limited (not your usage limit)" message at the same moment you've genuinely hit your limit, it now trusts your live usage meter — waits for the reset and resumes — instead of retrying into a wall and going silent. Pure temporary server hiccups still get a quick retry.

**Beta program + patch notes.** Updates now default to **stable-only**. Want early builds? Opt in via the gear menu's **"Join the beta program."** Every update ships its own patch notes — read them anytime from the gear's **"Patch notes,"** or the **"?"** next to any entry under Previous versions.

**Gear menu polish.** "Join the beta program" is now clearly highlighted, and "Remove Orbit" is red so the destructive option is unmistakable.

**Smoother updates.** Updating no longer nags you with an extra "wrapper update + restart" when only the patch tool changed — you get one update, not a restart treadmill.
