**Auto-continue keeps retrying transient rate-limits.** When a turn fails with a server "temporarily limiting requests (not your usage limit)" hiccup, Orbit now recognizes it even after it scrolls into the chat as the last message (before, it only saw the live banner). And transient retries now use a short, steady ~8–20s cadence instead of an exponential back-off that ballooned to two minutes — so a brief server limit no longer leaves the chat stalled.

**Auto-continue survives a reload** (recent): errored or usage-limited chats are remembered and continued after a window restart or developer reload — non-archived only.

**Auto-continue resumes after a usage limit resets** (recent): fixed the deadlock that left 5-hour-limited chats stuck even after the limit lifted.
