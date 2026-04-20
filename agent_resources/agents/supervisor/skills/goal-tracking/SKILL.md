---
name: goal-tracking
description: Manage projects, team tasks, and agent tasks through the Goals API.
---

# Goal Tracking Skill

## Overview
Use the Goals API to manage project hierarchy:
- `Project` for strategic goals
- `TeamTask` for supervisor-level execution
- `AgentTask` for delegated subagent work

## Common payloads

`FeedbackRequest`:
```json
{"feedback": "What to fix and why"}
```

`OutcomeRequest`:
```json
{"outcome": "Result summary"}
```

`FailureRequest`:
```json
{"failure_reason": "Concrete blocker"}
```

## Endpoints

### Overview and timeline
- `GET /goals/overview`
- `GET /goals/events/{target_kind}/{target_id}`

### Projects
- `POST /goals/projects`
- `POST /goals/projects/{project_id}/request-review`

### Team tasks
- `POST /goals/team-tasks`
- `PATCH /goals/team-tasks/{task_id}`
- `POST /goals/team-tasks/{task_id}/start`
- `POST /goals/team-tasks/{task_id}/fail`
- `POST /goals/team-tasks/{task_id}/request-review`
- `POST /goals/team-tasks/{task_id}/complete`
- `POST /goals/team-tasks/{task_id}/cancel`

### Agent tasks (supervisor)
- `POST /goals/agent-tasks`
- `POST /goals/agent-tasks/{task_id}/complete`
- `POST /goals/agent-tasks/{task_id}/reject-review`
- `POST /goals/agent-tasks/{task_id}/return-to-work`
- `POST /goals/agent-tasks/{task_id}/cancel`
- `POST /goals/agent-tasks/{task_id}/reopen`

### Agent-task collaboration
- `POST /goals/agent-tasks/{task_id}/progress`
- `POST /goals/agent-tasks/{task_id}/start`
- `POST /goals/agent-tasks/{task_id}/request-review`
- `POST /goals/agent-tasks/{task_id}/fail`

## Workflow: handling goal review prompt
1. Call `GET /goals/overview`.
2. For each `pending_review` task, verify result and decide complete/reject.
3. For each `failed` task, decide return-to-work/cancel/escalate.
4. When all team tasks are done, call `POST /goals/projects/{id}/request-review`.
5. Add progress notes on major decisions.

## Workflow: decomposing a new project
1. Create draft project.
2. Create draft team tasks linked to project.
3. After user approves execution, start team tasks.
4. Create and delegate agent tasks per team task.

## Workflow: handling failed task
1. Inspect failure reason and latest timeline entries.
2. If fixable, return to work with feedback.
3. If no longer useful, cancel.
4. If blocked by product/business decision, escalate to user.

## Guardrails
- Never complete projects directly; request user review.
- Do not edit approved (non-draft) projects/team tasks.
- Verify subagent output before confirming completion.
- Always include feedback for reject/reopen/return transitions.
- Cancellation cascades to non-completed children.
