**Copy diagnostics now sees errors that live in the chat — like "529 Overloaded."**

The first cut had a blind spot: when Claude shows an inline error in the conversation (e.g. *"API Error: 529 Overloaded — this is a server-side issue, usually temporary"*), the report wasn't looking there and wrongly said "no recent API failures." Now it captures the **recent message flow** and the **last error-flagged message**, and the summary names the error — a 529 is a transient *server overload*, not your usage limit (with the "error" toggle on, Auto-continue retries it on its own ~6s after the chat goes idle).

It's also honest about the host log now: Claude runs its API calls in a **separate CLI process**, so an empty "Host API log" is normal and expected — the new In-chat error section is the real source for these server errors.
