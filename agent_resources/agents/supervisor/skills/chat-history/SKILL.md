---
name: chat-history
description: "Read past chats and messages between the owner and the agent — recover conversation context, find what the owner said before, or look up exact wording. Use when the owner says 'what did we discuss', 'continue where we left off', 'find that thing I asked yesterday', 'remind me what I said about X', or any task requiring history beyond what memory files capture."
---

# What this covers

Read-only access to the owner's chat threads with this agent. Use it to look up what was actually said — not to send replies.

# Commands

**List chats** (most recently updated first):

```bash
sellerclaw agent-chat list
```

**List messages in a chat** — texts are truncated so the listing stays small.
Each item carries `text_truncated` and `text_total_chars`; the truncation cap is `text_preview_chars` (default 500, max 4000):

```bash
sellerclaw agent-chat list-messages <chat_id> [--offset 0] [--limit 50] [--text-preview-chars 500]
```

**Read a single message in full** — use when `text_truncated: true` in the listing, or when you need the message's `raw_content` (attachments, image/file parts):

```bash
sellerclaw agent-chat get-message <message_id>
```

# When to reach for it

- After a fresh session, before answering "as we discussed…" or "continue".
- When `MEMORY.md` / daily notes mention an exchange but lack specifics.
- To verify the exact wording of an owner ask before acting on it.

Don't use it as a substitute for memory files for facts that should be durable —
those still belong in `MEMORY.md`.
