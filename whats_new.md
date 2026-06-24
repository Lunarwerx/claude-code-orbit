**Everything new since the last stable — session fixes, a clearer Usage popover, and smarter auto-continue.**

**Sessions**
- **"New Session" actually starts a new session now.** The button (and the collapsed "+") could get stuck dumping you back on the chat you were already in — when "open conversations in a new tab" was on, it tried to open a separate editor tab that doesn't exist inside Orbit's single pane, so the click did nothing. It now always opens a fresh chat right here in the pane.
- **Refresh button is back** in the sessions header (next to Search), plus a gentle auto-refresh so the list keeps itself current instead of letting stale rows linger.
- **Running chats stop getting mislabeled "dead."** If a rate-limit warning was floating around (a different account or a subagent hit a limit), a live chat could show as limit-blocked in the list. Now a streaming chat shows its real activity — busy wins.

**Usage popover**
- **Time-left shows minutes** and ticks down live — "3h 59m left" / "21h 04m left" instead of a vague "3h."
- **Clearer labels:** the bright bold line is the countdown ("left"), and the window length is a quiet footnote ("5-hour window" / "7-day window") that can't be misread as "7 days 21 hours."
- Roomier, then right-sized — spans the sidebar without squishing the rings or wasting space.

**Auto-continue (opt-in — gear ▸ Auto-continue)**
- **No more phantom "keep going" on a stale limit flag.** When Claude's "you've hit your limit" flag latches for the full weekly window even though *this* account has headroom, auto-continue now trusts your live usage meter and stays quiet. Real limits and real errors still pause and resume exactly as before.

**Background tasks**
- Background command results now render as a tidy, dim **"⚙ Background task"** card instead of looking like a message you typed.

**"Show more"** no longer flickers in and out on hover — it shows whenever a message is genuinely clipped, and is simply absent when it isn't.
