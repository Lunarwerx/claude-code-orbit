**"New Session" now actually starts a new session.**

- Fixed the New Session button (and the collapsed "+") getting stuck — it would dump you right back on the chat you were already in instead of opening a fresh one.
- Cause: when "open conversations in a new tab" is on, the button was trying to open a separate editor tab, which doesn't exist inside Orbit's single pane — so the click did nothing. It now always opens the new session right here in the pane.
