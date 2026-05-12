---
name: task-management
description: "Plan, delegate, and supervise async trackable work via the goals system: create projects and tasks, review and approve or send back results."
---

# Task management (orchestrator)

Use this skill to **plan, delegate, and control** work via the goals system. The executor side (start / progress / request-review / fail) lives in the shared `tasks` skill.

## Entities

- **Project** — owner-level umbrella for related work. Optional.
- **Team task** — owner-visible piece of work. Frames a whole job. May live under a project.
- **Agent task** — concrete assignment to one subagent (`assigned_to`). Usually lives under a team task. This is what the subagent executes.

Relationship: `Project 1—* TeamTask 1—* AgentTask`. Each layer has its own id, status, and timeline.

## When to use the task system

Prefer tasks over a chat message whenever the work is bulky, multi-step, or anything the owner will want to see progress on. For one-off micro-asks a direct chat to the subagent is fine. Rule of thumb: if you would want to check on it in 10 minutes, create tasks.

## Plan and frame

```bash
sellerclaw agent-goals create-project --json-body '{
  "title": "...",
  "description": "...",
  "success_criteria": ["...", "..."]
}'

sellerclaw agent-goals create-team-task --json-body '{
  "title": "...",
  "description": "...",
  "project_id": "<project_uuid_or_omit>"
}'

sellerclaw agent-goals update-team-task <team_task_id> --json-body '{"description": "..."}'
sellerclaw agent-goals approve-team-task <team_task_id>
sellerclaw agent-goals start-team-task <team_task_id>
```

Schema for any `--json-body` command: `sellerclaw describe <operation_id>`.

## Delegate to a subagent

Create an agent task addressed to the picked subagent:

```bash
sellerclaw agent-goals create-task --json-body '{
  "title": "...",
  "description": "<everything the subagent needs: inputs, constraints, what to return>",
  "assigned_to": "<subagent_id, e.g. shopify / ebay / supplier>",
  "team_task_id": "<team_task_uuid>"
}'
```

`description` is the only place the subagent reads the brief — be explicit about inputs, constraints, and the expected outcome shape.

## Review and close

When the executor sets the task to `pending_review`:

```bash
sellerclaw agent-goals complete-task       <agent_task_id>
sellerclaw agent-goals reject-task-review  <agent_task_id> --json-body '{"feedback": "<actionable>"}'
sellerclaw agent-goals return-task-to-work <agent_task_id> --json-body '{"feedback": "<actionable>"}'
sellerclaw agent-goals reopen-task         <agent_task_id> --json-body '{"feedback": "<why>"}'
sellerclaw agent-goals cancel-task         <agent_task_id>
```

For the team task itself (after underlying agent tasks are done):

```bash
sellerclaw agent-goals request-team-task-review <team_task_id> --json-body '{"outcome": "<summary>"}'
sellerclaw agent-goals complete-team-task       <team_task_id> --json-body '{"outcome": "<final summary>"}'
sellerclaw agent-goals fail-team-task           <team_task_id> --json-body '{"failure_reason": "<why>"}'
sellerclaw agent-goals cancel-team-task         <team_task_id>
```

## Monitor

```bash
sellerclaw agent-goals get-overview
sellerclaw agent-goals list-my-tasks
sellerclaw agent-goals get-timeline <target_kind> <target_id>
#   target_kind: project | team_task | agent_task
```

`get-timeline` is the canonical audit feed — use it to see what the executor did, what feedback was given, why something was sent back.

## Guardrails

- Every agent task needs an `assigned_to` matching a real subagent id. Without it the task is unreachable.
- `complete-*` and `request-*-review` need a substantive `outcome`. Vague closes pollute the timeline and confuse the owner.
- Don't `fail-*` something the executor returned with a confusing outcome — use `reject-task-review` or `return-task-to-work` with feedback instead.
- For destructive control actions (`cancel-*`, `reopen-task` on a long-completed task), confirm with the owner first.

## Failure handling

- Validation error on `create-task` → most common miss is `assigned_to` or `team_task_id`.
- Task stuck in `pending_review` longer than expected → the executor is waiting on you; review or send back with feedback.
- Underlying agent tasks all `completed` but team task still open → close it explicitly with `complete-team-task` so the owner sees the job done.

