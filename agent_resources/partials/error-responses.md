## Error response conventions

All `sellerclaw-api` endpoints return errors in a consistent envelope.

### Standard error body

```json
{
  "detail": {
    "code": "ERROR_CODE",
    "message": "Human-readable description of the problem"
  }
}
```

### HTTP status → action

| Status | Meaning | Agent action |
|---|---|---|
| 400 | Bad request (invalid params, missing fields) | Fix the request and retry once |
| 401 | Auth failure (token expired or invalid) | Report blocker — do not retry |
| 403 | Forbidden (insufficient permissions) | Report blocker — do not retry |
| 404 | Resource not found | Verify the ID/path is correct; if valid, report as missing |
| 409 | Conflict (e.g., duplicate order, state transition violation) | Read current state from DB and adjust |
| 422 | Validation error (semantically invalid data) | Check field values and retry with corrected data |
| 429 | Rate limit exceeded | Wait briefly (5–10 seconds) and retry once |
| 500 | Internal server error | Retry once; if repeated, report blocker |
| 502/503 | Upstream service unavailable (CJ, Shopify, eBay, etc.) | Retry once; if repeated, report blocker with the upstream service name |
| 504 | Gateway timeout | Retry once; if repeated, report blocker |

### Retry policy

Max **two** retries on transient codes only (429, 500, 502, 503, 504). No retry on 400, 401, 403, 404, 409, 422. If exhausted — structured blocker (endpoint, status, code, message).
