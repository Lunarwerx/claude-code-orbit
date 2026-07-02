**Sidebar no longer calls a working chat "dead."**

If you had a chat streaming in the background (or even the one on screen) the sidebar could show it as idle/finished while it was clearly still working — so you couldn't trust the running dots when juggling more than one chat.

The status now reads from Claude's own per-session broadcast — the same signal that knows every chat's real state whether or not it's the one you're looking at — instead of a stale local flag that only tracked the attached chat. Running chats show as running (with their live activity text), waiting chats show as waiting, and a chat isn't marked done until it's actually done. Works across as many concurrent chats as you've got going.
