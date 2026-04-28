---
name: tasks
description: "Execute a task assigned to you in the goals system: see your queue, start work, log progress, request review, or fail with a reason. Use when working as a sub-agent (shopify, ebay, supplier, etc.) and the user or session start indicates assigned work."
---

# Tasks (executor)

This skill covers the **executor** side of the goals system: how to pick up assigned work, report on it, and hand it back for review. Use only when running as a sub-agent executor. Planning, creating, and reviewing tasks is a separate role — the supervisor's `task-management` skill.

## Entity recap

- An **agent task** is a unit of work assigned to you (`assigned_to == <your agent id>`).
- It usually lives under a **team task** (owner-visible) and optionally a **project** (the strategic frame).
- The states you participate in: `created → in_progress → pending_review`. Final states (`completed` / `failed` / `cancelled`) are set by the creator after you finish — except `fail-task`, which you may call yourself when truly blocked.

## At session start

```bash
sellerclaw agent-goals list-my-tasks
```

Anything `created` or `in_progress` assigned to you is yours to act on.

## Lifecycle commands

```bash
sellerclaw agent-goals start-task <agent_task_id>

sellerclaw agent-goals add-progress-note <agent_task_id> \
  --json-body '{"message": "<what just happened or what you decided>"}'

sellerclaw agent-goals request-task-review <agent_task_id> \
  --json-body '{"outcome": "<thorough summary: what was done, key numbers, links to artifacts>"}'

sellerclaw agent-goals fail-task <agent_task_id> \
  --json-body '{"failure_reason": "<concrete blocker>"}'
```

Schema for any `--json-body` command: `sellerclaw describe <operation_id>`.

## Reading the timeline

If the creator sends the task back via `reject-task-review` or `return-task-to-work`, the task moves back to `in_progress` with feedback recorded in the timeline. Read it before continuing:

```bash
sellerclaw agent-goals get-timeline agent_task <agent_task_id>
```

Address the feedback, then `request-task-review` again. If the creator calls `reopen-task` on a closed task, treat it as a fresh `in_progress` round and follow the lifecycle from the top.

## Rules

- Start before doing work; the `start` event matters for review.
- Add a progress note after each significant step — the reviewer reads them.
- You **cannot self-complete**. End with `request-task-review`; the creator calls `complete-task`.
- `outcome` cannot be "Done" / "OK". The reviewer reads only `outcome`, not the chat. Include findings, metrics, links.
- `fail-task` requires a concrete `failure_reason`. Ambiguous task → `request-task-review` with an honest outcome explaining the ambiguity, not `fail`.

## Failure handling

- `403` on a lifecycle call → you are not the assignee. Check `list-my-tasks` again.
- Validation error → re-check required fields (`outcome` for review, `failure_reason` for fail, `message` for progress). The CLI returns the rejected paths.
