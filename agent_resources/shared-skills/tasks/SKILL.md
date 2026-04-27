---
name: tasks
description: "Work with the goals / task system — projects, team tasks, and agent (subagent) tasks. Use when the user says 'create a task', 'track this work', 'what am I assigned', 'review the task', 'mark it done', 'fail the task', or when you need to delegate nontrivial work asynchronously, record progress, or close out an assignment."
---

# Tasks

The goals system tracks work asynchronously and auditably. Every agent — the supervisor and every subagent — uses the same entities, just from different sides.

## Entities

- **Project** — umbrella for related work (owner-level strategic goal). Optional; many tasks live without a project.
- **Team task** — one concrete piece of work, owner-visible. Can live under a project or standalone. Created by the supervisor (or owner) to frame a whole job.
- **Agent task** — assignment of work to one specific agent (`assigned_to`). Usually lives under a team task (`team_task_id`). This is what a subagent actually executes and reports on.

Relationship: `Project 1—* TeamTask 1—* AgentTask`. Each layer has its own id, its own status, its own event timeline.

## Actors

- **Creator / reviewer** — the agent who created the task and reviews its outcome. Typically the supervisor for everything below it.
- **Executor** — the subagent named in `assigned_to` on an agent task. Does the work, reports progress, requests review.

## Lifecycle

**Statuses (order):** `created` → `in_progress` → `pending_review` → `completed`. From allowed earlier states you can also land in `failed` or `cancelled`.

**Executor:** `start-`* when you take the work; `add-progress-note` while running; finish with `request-*-review` (outcome for the reviewer) or `fail-*` (concrete `failure_reason`). Do **not** call `complete-`* on work you execute yourself.

**Creator / reviewer:** when `pending_review`, either `complete-`* or send back via `reject-task-review` / `return-task-to-work` (agent tasks). Everything else (`reopen-task`, `cancel-*`, team-task approve/start/`request-team-task-review`/complete/fail) — see **Manage / review** below.

## CLI group

All operations live under `sellerclaw agent-goals`.

### As the creator / reviewer (supervisor)

Plan and frame:

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
```

Delegate to a subagent by creating an agent task addressed to it:

```bash
sellerclaw agent-goals create-task --json-body '{
  "title": "...",
  "description": "<everything the subagent needs to act: inputs, constraints, what to return>",
  "assigned_to": "<subagent_id, e.g. shopify / ebay / supplier>",
  "team_task_id": "<team_task_uuid>"
}'
```

Manage / review:

```bash
sellerclaw agent-goals approve-team-task <team_task_id>
sellerclaw agent-goals start-team-task <team_task_id>
sellerclaw agent-goals request-team-task-review <team_task_id> --json-body '{"outcome": "<summary>"}'
sellerclaw agent-goals complete-team-task <team_task_id>  --json-body '{"outcome": "<final summary>"}'
sellerclaw agent-goals fail-team-task     <team_task_id>  --json-body '{"failure_reason": "<why>"}'
sellerclaw agent-goals cancel-team-task   <team_task_id>

# agent-task review:
sellerclaw agent-goals complete-task       <agent_task_id>
sellerclaw agent-goals reject-task-review  <agent_task_id> --json-body '{"feedback": "<actionable>"}'
sellerclaw agent-goals return-task-to-work <agent_task_id> --json-body '{"feedback": "<actionable>"}'
sellerclaw agent-goals reopen-task         <agent_task_id> --json-body '{"feedback": "<why>"}'
sellerclaw agent-goals cancel-task         <agent_task_id>
```

### As the executor (subagent)

```bash
sellerclaw agent-goals list-my-tasks                     # defaults to the caller
sellerclaw agent-goals list-my-tasks --agent-id <self>   # explicit

sellerclaw agent-goals start-task <agent_task_id>

sellerclaw agent-goals add-progress-note <agent_task_id> \
  --json-body '{"message": "<what just happened>"}'

sellerclaw agent-goals request-task-review <agent_task_id> \
  --json-body '{"outcome": "<thorough summary: what was done, findings, metrics, links>"}'

sellerclaw agent-goals fail-task <agent_task_id> \
  --json-body '{"failure_reason": "<concrete blocker>"}'
```

### Observation (both roles)

```bash
sellerclaw agent-goals get-overview
sellerclaw agent-goals list-my-tasks
sellerclaw agent-goals get-timeline <target_kind> <target_id>
#   target_kind: project | team_task | agent_task
```

## When to use tasks instead of a direct chat message

- **Direct message to the subagent** — OK for quick, small, synchronous work: resolve an id, look up one listing, publish a couple of products. The conversation itself is the trace.
- **Tasks** — required when the work is **nontrivial or bulky**: large batch, multi-step, enrichment-heavy, or anything the owner will want visible and reviewable. Create a team task to frame the whole job + at least one agent task addressed to the executing subagent. This makes execution asynchronous, auditable via `get-timeline`, and owner-visible in the Goals UI.

Rule of thumb: if the answer to "would I want a status check in an hour?" is yes, use tasks.

## Guardrails

- Never self-complete. Executors call `request-task-review`; the creator decides.
- Never submit `outcome` as "Done" / "OK". Reviewer reads only `outcome`, not chat history — include what was done, key numbers, links to artifacts.
- Never `fail-`* without a concrete `failure_reason`. Agent tasks that are unclear go to `request-task-review` with an honest outcome, not `fail`.
- Never create an agent task without an `assigned_to` — the API will reject it, and an unassigned task is unreachable.
- Never bypass the task system for bulky work that the owner will want to see progress on.

## Failure handling

- `403` on an agent-task lifecycle call → you are not the assignee; check `list-my-tasks` to confirm which agent owns it.
- Task stuck in `pending_review` → the creator has not reviewed yet; nudge via a progress note on the team task, or ping in chat.

