**Fix: Auto-continue no longer gets stuck on a stale "usage limit" banner.** This is the bug behind "auto-continue never works" — and the new Copy diagnostics button is what caught it.

What was happening: after a usage limit reset, Claude's **"You've hit your usage limit"** banner can linger on screen even though your live meter is back down with plenty of headroom. Auto-continue trusted that stale banner, decided "I'll wait for the reset" — but since there was no real maxed window to wait on, it could never schedule a resume and just sat there forever, never sending "keep going." Now the **live usage meter is the tiebreaker**: if it shows headroom, auto-continue treats the lingering banner as stale and resumes the chat (which also clears the banner).

**Copy diagnostics now names this case.** If a chat shows a usage-limit banner while the meter isn't actually maxed, the report calls it out as a stale banner instead of saying "nothing wrong" — so it's obvious at a glance.
