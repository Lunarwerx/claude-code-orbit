**Account switcher fixes — no more duplicate accounts.**

The switcher could show the same account twice (once active, once as a removable copy). The cause: an account was partly identified by its login token, which changes each time it refreshes — so a refresh spawned a second copy of the same account.

- **Stable identity.** Accounts are now identified by their account ID, which never changes, so the same account can't duplicate itself.
- **Auto-cleanup.** Any duplicates from before are automatically merged the next time you open the switcher — the extra copy disappears on its own.
- **Clearer remove button.** The "×" to forget an account is now always visible on the accounts you're not using.

Certified against Claude Code 2.1.198.
