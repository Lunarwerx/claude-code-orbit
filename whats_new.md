**Chat list fix — no more duplicate sessions.**

Sometimes the sidebar showed the same chat stacked several times over — and it grew every time you sent a message. Clicking one copy would make a *different* one start working, or make the one you clicked go dead, and a few couldn't be clicked at all. A window reload cleared it, but only for a while.

The cause was in our sidebar, not your chats. Claude briefly keeps more than one in-memory copy of the chat you're actively working in, and we were drawing every copy as its own row. **Your chats were never actually duplicated and nothing was lost** — every conversation is still a single, intact file.

- **Each chat now shows exactly once.** We collapse duplicate copies by the chat's stable ID before drawing the list. A genuinely different chat can never be merged in — two different conversations never share an ID.
- **The right copy stays.** When duplicates exist we keep the one you're attached to (or the one that's actively streaming), so a live chat never vanishes or loses its highlight.
- **Clicking is reliable again.** Rows are now tracked by stable ID instead of list position, so when the list re-sorts (busy chats jumping to the top) a click can't land on the wrong chat.

Certified against Claude Code 2.1.199.
