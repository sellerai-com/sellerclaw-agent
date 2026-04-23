---
name: owner-notifications
description: "Send structured owner notifications about critical events, actions, and operational updates."
---

# Owner Notifications Skill

## Goal
Send concise and actionable notifications to the owner via the user's preferred channel.

## Communication channels

The owner has two possible communication channels:

- **sellerclaw-ui** — the web dashboard chat (default).
- **telegram** — Telegram direct messages (available when the owner has connected a Telegram bot).

The owner chooses a **primary channel** in their settings. When you proactively notify the owner (not replying in an existing conversation thread), always send the message to the primary channel so it reaches the owner where they expect it. The primary channel value is available in the runtime config as `primaryChannel`.

Critical system alerts may arrive via hooks with `deliver` already set by the backend; do not duplicate those unless you are adding new context.

## Severity model

### CRITICAL (immediate)
- API outages or repeated auth failures.
- Fulfillment blockers.
- Time-sensitive failures affecting active orders.

Format:
```
🚨 CRITICAL

{issue summary}
Impact: {affected entities}
Action needed: {clear next step}
```

### ACTION (immediate)
- Decision required (approve/reject, retry/cancel/manual).
- Blocking ambiguity requiring owner choice.

Format:
```
⚡ ACTION

{what needs a decision}
Options: {option1} / {option2} / {option3}
```

### INFO (can batch)
- Completed operations.
- Non-blocking updates.
- Session digest/report delivery.

Format:
```
ℹ️ INFO

{summary}
{optional metrics}
```

## Rules
- Avoid duplicate notifications for the same event in one session.
- Group related events (for example, multiple new orders) in one message.
- Keep messages short and structured.
- Never leak tokens or private secrets.

## Automated Process Notifications

Use these templates for system-driven workflows:

**Auto-fulfillment completed (INFO):**
```
ℹ️ INFO

Auto-fulfillment completed for order {order_id} on {platform}.
Tracking: {tracking_number}
```

**Auto-fulfillment failed (CRITICAL):**
```
🚨 CRITICAL

Auto-fulfillment failed for order {order_id}.
Reason: {error}
Action needed: manual fulfillment is required.
```

**Stock/price sync anomalies (ACTION or INFO):**
```
⚡ ACTION

Stock/price sync detected significant changes.
Affected products: {count}
Decision needed: review pricing/stock strategy?
```

```
ℹ️ INFO

Stock/price sync completed.
Updated products: {count}, price changes: {price_changes}, stock-outs: {stock_outs}.
```

**Sync failures (CRITICAL):**
```
🚨 CRITICAL

Marketplace sync failed for channel {channel_name}.
Reason: {error}
Action needed: investigate integration and retry.
```
