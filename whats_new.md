**"New Session" no longer bounces you back to the old chat.**

- Fixed a follow-on to last build's New Session fix: the button would open the fresh chat, then a couple seconds later snap you right back to the chat you came from.
- Cause: a brand-new chat has no saved id yet, so the address the pane remembers ("which chat is active") still pointed at the old one — and the every-few-seconds session refresh kept restoring it. The button now clears that pointer when it opens a new chat, so the new one sticks. The moment you send your first message, the new chat gets its real id and everything tracks normally again.
