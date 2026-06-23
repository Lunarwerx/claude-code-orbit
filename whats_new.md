**Auto-continue no longer fires on a stuck/stale usage-limit flag.**

You caught this one: when Claude's "You've hit your weekly limit" flag latches onto a chat — e.g. a *different* account or a subagent hit the limit, so the flag sticks for the full ~3-day weekly window even though **this** account has plenty left — Auto-continue was reading that stuck flag and sending "keep going" on its own (sometimes right when the AI was handing off to you).

Now Auto-continue trusts your **live usage meter** instead of the flag: if the meter shows you actually have headroom, a stale limit flag is treated as stale and **nothing auto-sends**. Real usage limits and real errors still pause and resume exactly as before — this only stops the phantom "keep going" on a chat that isn't truly limited.

*(Auto-continue is opt-in — gear ▸ Auto-continue — and you can turn any of its toggles off there.)*
