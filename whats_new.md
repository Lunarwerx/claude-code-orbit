**Account switching fix — no more "switched… and then logged out".**

Switching accounts used to say "Switched" and then, about 20 seconds later, Claude could log you out entirely. The cause: an account that sits saved while you use another one goes stale — its access token quietly expires after a few hours. Switching swapped that expired token in as-is, and Claude's next authentication check killed the session.

- **Switching now refreshes the account first.** If the saved sign-in has gone stale, we renew it (the same way Claude itself renews tokens) *before* swapping it in — so the account you switch to always arrives with a live sign-in. No extra clicks, no reload.
- **The renewed sign-in is saved back**, so every account in your list stays healthy no matter how long it sits unused. Opening the account list also quietly revives stale accounts in the background (which fixes their usage bars too).
- **If an account truly can't be renewed** (its sign-in was revoked), the switch no longer half-happens and logs you out — it stays on your current account and tells you plainly to remove that entry and re-add the account.
- Also fixed: signing out via "Add account" could save a broken, empty entry over a good one. That can't happen anymore, and any such entry heals itself automatically.

Certified against Claude Code 2.1.200.
