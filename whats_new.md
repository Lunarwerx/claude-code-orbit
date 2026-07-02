Performance pass — long chats and idle churn:

- **Faster long chats.** The copy-button scanner no longer rescans every message in the chat every 1.5 seconds — it now only looks at new messages, with a light self-heal pass every 30 seconds. Big conversations stop getting slower as they grow.
- **Less background churn.** The session list now refreshes every 25 seconds instead of every 7, and checks your cloud sessions half as often — fewer network calls and fewer sidebar re-renders while you're just working.
- **Smoother idle.** Auto-resume's error checks no longer force the page to re-measure its layout every few seconds while a chat sits idle.
- **Lighter zombie reaper.** When no Claude processes are running, the reaper now exits instantly instead of doing a full system process scan every 3 minutes.
- Certified against Claude Code 2.1.198.
