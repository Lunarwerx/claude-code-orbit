**YOLO mode is now per-chat — it can't leak into your other chats.**

- The first YOLO build auto-approved across **every** open chat at once (it was a single global switch). Fixed: YOLO is now scoped to the one chat you turn it on in — exactly like the native Ask / Plan / Edit modes. Turning it on in one chat does nothing to the others.
- Under the hood the auto-approve decision now happens per chat (keyed by that chat's channel) right in the approval handler, so there's no global flag left to leak.
- Note: turn it on from inside a chat that's already going (one you've sent at least one message in). Making brand-new chats *start* on YOLO is coming with the Default settings popup in the next build.
