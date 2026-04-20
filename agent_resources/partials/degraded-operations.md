## Degraded operations playbook

When external services are unavailable or the system is under stress, follow these
guidelines to maintain useful operation and communicate clearly.

### Service degradation scenarios

| Scenario | Detection | Agent action |
|---|---|---|
| **Supplier API down** (503 from `/suppliers/{provider}/...`) | 2+ consecutive failures on supplier endpoints | Stop purchase attempts. Notify owner: "Supplier API ({provider}) is currently unavailable. Orders will be queued and processed when service resumes." Queue affected order_ids for retry. |
| **Store API down** (502/503 from store platform endpoints) | 2+ consecutive failures on platform endpoints | Stop store operations. Notify owner: "Store connection ({platform}) is experiencing issues. Order sync and fulfillment are paused." |
| **Ad platform auth expired** (401 from `/ads/{provider}/...`) | Auth error on any ad endpoint | Mark integration as `TOKEN_EXPIRED`. Notify owner immediately: "Your {platform} ad account needs re-authorization. Active campaigns continue running but I can't monitor or optimize until reconnected." |
| **Browser unresponsive** | Browser action times out or errors repeatedly | Fall back to advisory mode for browser-dependent tasks. Report: "Browser is unresponsive. I can provide text-based analysis but cannot access external websites right now." |
| **Rate limiting** (429 responses) | Rate limit hit on any endpoint | Back off for 10-30 seconds. If repeated, reduce operation pace. Do not retry aggressively. |
| **High order volume** | 10+ pending orders with slow subagent responses | Process orders in batches of 5. Prioritize by age (oldest first). Notify owner of queue depth. |

### Communication during degradation

Use the `🚨 CRITICAL` notification format:

```
🚨 CRITICAL — Service Degradation

{Service}: {issue description}
Impact: {what's affected}
Current status: {queued N orders / paused operations / etc.}
Action needed: {what the owner should do, if anything}
Auto-recovery: {yes — will retry automatically / no — manual intervention needed}
```

### Recovery behavior

- When a previously failed service starts responding, process queued work automatically.
- Notify the owner when normal operations resume: "ℹ️ INFO — {Service} is back online. Processing {N} queued items."
- Do not replay failed notifications — only report new results from the recovery batch.

### Timeout thresholds

| Operation | Expected duration | Timeout | Action on timeout |
|---|---|---|---|
| API call (single) | <5 seconds | 30 seconds | Retry once, then report blocker |
| Subagent task (simple) | <30 seconds | 2 minutes | Use **`sessions_history(childSessionKey)`** to verify progress; **`sessions_send`** nudge if idle. Re-delegate only after the prior child session has **ended** unsuccessfully. If still running past the threshold, report the delay to the owner — do **not** spawn a duplicate parallel run |
| Subagent task (complex) | <2 minutes | 5 minutes | **`sessions_history(childSessionKey)`** periodically; report delay to owner if no progress; never duplicate a running child with another **`sessions_spawn`** |
| Browser page load | <10 seconds | 30 seconds | Skip page, report as "unresponsive" |

### Fallback hierarchy

Degraded: **API** (if exists) → **browser** workaround → **advisory** + queue → **escalate** to owner if blocks persist across tasks.
