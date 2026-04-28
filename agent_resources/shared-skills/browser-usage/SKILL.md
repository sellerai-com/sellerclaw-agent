---
name: browser-usage
description: "How to use the browser tool responsibly: when it is appropriate, what to expect, and how to ask the user for help when blocked. Read this skill before using the browser tool for the first time in a session, or whenever browser actions start failing."
---

# Browser Usage

Browser exists to cover external web
resources that have no integration — not to bypass an existing API.

## Expect instability

UI, lazy load, and interstitials drift; anti-bot (CAPTCHA, device checks,
blocks) and expiring sessions break flows; selectors can silently point at the
wrong element. Assume retries, not a clean run — after two failures with a
reasonable recovery attempt, stop looping and treat it as blocked.

## Ask the user when access breaks

The user can interact with the agent's browser session directly. When the
browser hits something you cannot solve on your own, **ask for help** instead of
giving up:

- **Login wall** — ask the user to sign in to the target resource in the
  agent's browser. After they authenticate, the session persists and you can
  continue the task.
- **CAPTCHA / 2FA / device check** — ask the user to solve the challenge in the
  browser.
- **Region, permission, or anti-bot blocks** — surface the exact URL and the
  error you see, and ask the user how to proceed (switch account, skip, etc.).

Be specific: name the URL, the action that is blocked, and what the user has to
do. "I can't access this site" is not actionable.

If the user's assistance unblocks the flow, continue and report the result as
usual. If the browser keeps failing after help — or the user is not reachable —
report the task as blocked with the failing step and the error you saw, rather
than silently degrading the result.
