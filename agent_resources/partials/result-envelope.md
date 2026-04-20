## Subagent result envelope

Return results to the supervisor in this structure so responses stay parseable.

### Required shape

```json
{
  "status": "success | partial | failed",
  "summary": "1-3 bullet points describing what was done",
  "data": {},
  "evidence": {},
  "files": [],
  "errors": [],
  "next_step": "recommended follow-up action"
}
```

| Field | Type | Required | Notes |
|---|---|---|---|
| `status` | string | yes | `success` = all tasks completed, `partial` = some succeeded, `failed` = none succeeded |
| `summary` | string | yes | 1–3 bullet points, concise |
| `data` | object | yes | Agent-specific structured data (scores, candidates, metrics, IDs) |
| `evidence` | object | no | Raw data backing the conclusions: trend values, API response excerpts, URLs visited, product IDs and prices, timestamps. Supervisor uses this to substantiate claims to the user. Include source labels (e.g. "google_trends", "cj_catalog", "facebook_ad_library"). |
| `files` | array | no | `[{"name": "report.csv", "download_url": "..."}]` — use File Storage skill to upload |
| `errors` | array | no | `[{"code": "...", "message": "...", "context": {}}]` — include endpoint/http_status when relevant |
| `next_step` | string | yes | Approve, retry, escalate, monitor |

