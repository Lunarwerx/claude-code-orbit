**A real YOLO mode — and this one actually works.**

- Last build removed the old "YOLO mode" because it never did anything: it was Claude's native bypass mode, which is broken in the VS Code extension (the approval prompt fires no matter which mode you pick).
- This adds a *real* one: **gear menu ▸ YOLO mode**. When it's on, Orbit auto-approves every permission prompt — file edits, terminal commands, MCP tools — before it can even appear. It works by intercepting the approval at the host layer, so it doesn't depend on the native mode that's broken. Unbreakable, because it just says yes.
- **Off by default.** It asks you to confirm when you turn it on, and shows a red **"YOLO MODE - auto-approving everything"** badge the whole time it's running, so you can never forget it's on.
- Heads up: this genuinely skips **all** safety prompts, including destructive commands. Use it only when you trust what's running — flip it off any time to bring the prompts back.
