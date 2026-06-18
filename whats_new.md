**Auto-continue now survives a reload.** If a chat stops on an error or a usage limit, Orbit remembers it — so even after a window restart or a developer reload (which wipes the live error signal), auto-continue picks the chat back up and continues it on its own. It only ever does this for **non-archived** chats, and only from a **real** stop (a genuine error or the usage-limit banner) — never a chat that merely *mentioned* an error in its text.

**Auto-continue resumes after a usage limit resets** (recent): fixed a deadlock where a chat stopped by your 5-hour limit never auto-resumed even after the limit lifted, because "resume" waited on a banner that only clears once a turn starts.

**Per-project effort** (recent): the Default effort slider remembers its setting per project, instead of one machine-wide value.
