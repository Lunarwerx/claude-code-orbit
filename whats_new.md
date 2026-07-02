Everything new since the last stable — YOLO mode made reliable, the sidebar made honest, and a performance pass.

**YOLO mode now works the way it should.**
- **It stays on.** Turn YOLO on for a chat and it stays on for that whole chat — every file edit, terminal command, and tool runs without asking — while every other chat keeps prompting like normal. (It used to quietly forget the setting after each turn.)
- **It never silently slips.** Every permission request in a YOLO chat is now matched to your chat at the source the instant a turn starts, so a prompt can't sneak through anymore.
- **It still lets the AI ask you questions.** YOLO waves through every real action — edits, commands, deletes — but when the AI needs you to *decide* something (a "which approach?" question or a plan to approve), that now surfaces so you can answer it. Actions: automatic. Questions: still yours.

**The sidebar no longer calls a working chat "dead."**
- A chat streaming in the background — or even the one on screen — could show as idle/finished while it was clearly still working. Status now reads from Claude's own per-session signal that knows every chat's real state, so running chats show running (with live activity), waiting chats show waiting, and nothing is marked done until it actually is. Works across as many concurrent chats as you've got going.

**Performance pass — long chats and idle churn.**
- **Faster long chats.** The copy-button scanner only looks at new messages now instead of rescanning the whole conversation every 1.5 seconds, so big chats stop getting slower as they grow.
- **Less background churn.** The session list refreshes less aggressively and checks your cloud sessions half as often — fewer network calls and re-renders while you work.
- **Smoother idle** and a **lighter background cleanup** process that stays out of the way when nothing's running.

Certified against Claude Code 2.1.198.
