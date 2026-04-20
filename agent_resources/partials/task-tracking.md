## Task tracking

You may have assigned tasks in the goal tracking system. When working on a task:

- Start work with `POST /goals/agent-tasks/{id}/start`.
- Report progress with `POST /goals/agent-tasks/{id}/progress`.
- Signal completion with `POST /goals/agent-tasks/{id}/request-review`
  and include a clear `outcome`.
- Signal blockers with `POST /goals/agent-tasks/{id}/fail` and
  include a clear `failure_reason`.

On every Goals API request, add `-H "X-Agent-Id: {{agent_id}}"` so the API
identifies you correctly. Without it, calls default to `supervisor` and task
lifecycle endpoints (`start`, `progress`, `request-review`, `fail`) return **403**
when the task is assigned to a different agent.

You cannot complete your own tasks. You can only request review.

If review is rejected or task is returned to work, read the feedback and address it directly.

Check your assigned tasks at session start:
`GET /goals/my-tasks`.

Full endpoint reference is in the `task-reporting` skill.
