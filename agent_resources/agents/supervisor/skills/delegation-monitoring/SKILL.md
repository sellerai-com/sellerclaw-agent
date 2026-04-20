---
name: delegation-monitoring
description: Monitor delegated subagent work using OpenClaw session tools — avoid blind retries and duplicate spawns.
---

# Delegation monitoring

## Goal

After you delegate work with `sessions_spawn`, **actively monitor** the child session until it finishes or clearly failed. Do not assume failure just because a step feels slow.

## After every `sessions_spawn`

1. Save the returned **`childSessionKey`** and **`runId`** (if present) in your working context for this task.
2. Prefer one child session per logical task — do not spawn a second parallel session for the same work while the first is still active.

## Checking child session progress

Use OpenClaw session tools (from `group:sessions`):

1. **`sessions_list`** — find recent / active sessions; locate the child session tied to your delegation (match on key, recency, or agent id).
2. **`sessions_history`** — read the transcript for **`childSessionKey`** (optionally `includeTools: true` to see tool calls and errors).
3. If the child is **active but quiet** for longer than expected, send a short nudge with **`sessions_send`** to `childSessionKey` (e.g. ask for status / blockers), with a reasonable `timeoutSeconds` (e.g. 30–60). Prefer waiting over spamming.

## Decision matrix

| Child session state | What to do |
|---------------------|------------|
| Active and transcript shows steady progress | Keep waiting; optionally notify the owner if SLA is exceeded. |
| Active but no new events for a long time | `sessions_history` again; then `sessions_send` nudge; if still stuck, escalate to the owner. |
| Ended successfully (result in transcript / announce) | Process the outcome; do not re-delegate the same work. |
| Ended with error or timeout | Read the error in history **and** progress from Goals API; **then** retry at most **2** times with a clear change (narrower scope, different inputs, passing partial results), or escalate. |
| You are unsure whether the first child is still running | **`sessions_list` + `sessions_history`** before any new `sessions_spawn` for the same task. |

## Recovering from timeout / failure with Goals API

When a child session ends with a timeout or error, the session transcript may be
incomplete or lost. However, if the delegation used an **AgentTask** (see
`product-scout-delegation` and other delegation skills), the subagent's progress
checkpoints are **persisted in the DB** and survive session crashes.

### Recovery steps

1. **Read session history** — `sessions_history(childSessionKey, includeTools: true)`.
   Check what the subagent accomplished before the failure.
2. **Read task progress** — `GET /goals/events/agent_task/{task_id}` (endpoint:
   `/goals/events/{target_kind}/{target_id}` with `target_kind=agent_task`). This returns
   all progress notes the subagent posted via `POST /goals/agent-tasks/{id}/progress`.
   These checkpoints contain intermediate results (e.g. "Trends collected for 5 niches:
   ...", "CJ products found for niches 1–3: ...").
3. **Combine both sources** into a summary of completed vs remaining work.
4. **Re-spawn with continuation context** — pass the partial results in the new task
   description so the subagent picks up where it left off instead of starting from
   scratch. Include `agent_task_id` so progress continues on the same task.
5. **Do not create a new AgentTask** for the retry — reuse the existing one.

### When to use Goals API vs session tools

| Data needed | Source |
|---|---|
| Is the child still running? | `sessions_list` / `sessions_history` |
| What did the child do (real-time)? | `sessions_history(includeTools: true)` |
| What intermediate results survived a crash? | `GET /goals/events/agent_task/{task_id}` (i.e. `/goals/events/{target_kind}/{target_id}`) |
| Should I retry or escalate? | Both — combine session error with progress checkpoints |

## Digest triage — fast NO_REPLY

When you receive a system digest about Agent Task progress for a Team Task:

1. Check how many Agent Tasks for this Team Task are **still running** (status `in_progress` or `pending`).
2. If **any** Agent Tasks are still in progress → reply `NO_REPLY` immediately. Do not read skills, do not analyze data, do not check sessions. Just reply `NO_REPLY`.
3. Only proceed to scoring/reporting when **all** Agent Tasks for the Team Task have reached a terminal status (`completed`, `failed`, or `cancelled`).

This avoids wasting LLM tokens on intermediate awakening — the supervisor will be notified again when the next Agent Task completes.

## Anti-patterns (never do)

- **Lowering `runTimeoutSeconds`** on retries — the gateway config sets a minimum; do not undercut it.
- **Restarting from scratch** when progress checkpoints exist — always pass partial results to the retry.
- **Creating a new AgentTask** for a retry — reuse the existing task so progress history is continuous.
- **Emitting monitoring status as text** — monitoring is silent; the user sees only the final result or "Still working on it."

## Relation to SellerClaw APIs

- **DB / orders / goals** — use the system API (`exec curl`) for business state and task progress.
- **OpenClaw delegation** — use **`sessions_spawn`**, **`sessions_list`**, **`sessions_history`**, **`sessions_send`** for what happened inside the child agent session.

When both apply, use **session tools** to decide whether a delegation is still running vs failed, and **Goals API** to recover intermediate results for retries.
