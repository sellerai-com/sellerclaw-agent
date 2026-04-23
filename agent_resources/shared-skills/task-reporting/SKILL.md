---
name: task-reporting
description: "Report progress and status changes on assigned agent tasks."
---

# Task Reporting Skill

## Goal
Use the Goals API to keep assigned agent tasks up to date for supervisor review.

## Agent identification (`X-Agent-Id`)

All Goals API requests **must** include `-H "X-Agent-Id: {{agent_id}}"` so the
server knows which agent is acting. This applies to every endpoint, including
`GET /goals/my-tasks` (used to filter tasks by your agent id). Without the header
the API defaults to `supervisor`: lifecycle endpoints (`start`, `progress`,
`request-review`, `fail`) return **403** when the task is assigned to a different
agent, and `my-tasks` returns the supervisor's task list instead of yours.

Example:

```bash
curl -s -X POST "{{api_base_url}}/goals/agent-tasks/${TASK_ID}/progress" \
  -H "Authorization: Bearer $AGENT_API_KEY" \
  -H "X-Agent-Id: {{agent_id}}" \
  -H "Content-Type: application/json" \
  -d '{"message":"Checkpoint: trends collected for 5 keywords."}'
```

## Endpoints
- `GET /goals/my-tasks`
- `POST /goals/agent-tasks/{task_id}/start`
- `POST /goals/agent-tasks/{task_id}/progress`
- `POST /goals/agent-tasks/{task_id}/request-review`
- `POST /goals/agent-tasks/{task_id}/fail`

## Payloads

Request review payload:
```json
{"outcome": "Detailed summary of what was done, key findings, and links to any created documents or files."}
```

Fail payload:
```json
{"failure_reason": "What blocked completion"}
```

Progress payload (field name is **`message`**, not `note`):
```json
{"message": "Current progress update"}
```

## Status guide
- `IN_PROGRESS`: task execution is underway.
- `PENDING_REVIEW`: task is done and awaits supervisor verification.
- `FAILED`: blocked from completion; describe blocker in `failure_reason`.

## Rules
- Check assigned tasks at the start of each session.
- Start a task before doing work.
- Add progress notes after significant work chunks.
- Always provide outcome when requesting review. The outcome must be a thorough summary: include key findings, results, metrics, and links to any created documents or files. Never submit a vague outcome like "Done" — the reviewer reads only the outcome field, not the chat.
- Always provide failure reason when marking failed.
- You cannot complete your own tasks.
- Read supervisor feedback and act on it when review is rejected.
