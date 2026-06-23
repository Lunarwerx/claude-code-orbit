**Two fixes from your feedback — and the drag-and-drop file tray is up next.**

- **Running chats no longer get mislabeled "dead."** If a chat was actively working but a rate-limit warning was floating around (e.g. a *different* account or a subagent hit a limit), Orbit was tagging the live chat as limit-blocked in the session list. Now **"busy" wins** — a streaming chat shows its real activity instead of a stale limit message.
- **Background task-notifications now look like tasks, not a debug log you typed.** When a command runs in the background, Claude Code feeds its result back into the chat as a `<task-notification>` (that's native Claude, not a message you sent). Orbit now reframes those as a tidy **"⚙ Background task"** card with the summary, so they stop reading like something you wrote.

**Coming in the next beta:** drag files from the Explorer onto the composer to reference them, each with an eyeball toggle to include/exclude.
