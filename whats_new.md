**Removed "YOLO mode" — it never actually worked.**

- "YOLO mode" in the composer's Modes menu was just Claude's native "Bypass permissions" mode with our label on it. We also force-added it to the menu and defaulted new chats into it.
- The catch: that native mode is **broken in the VS Code extension** — Claude Code's own code still pops the approval prompt no matter which mode you pick (it's an open bug on Anthropic's side), so "YOLO mode" never actually skipped anything.
- Rather than keep a button that pretends to work, we removed our YOLO label and the forcing entirely. The Modes menu is now exactly Claude's native set, behaving exactly as native Claude Code intends — nothing else changes.
