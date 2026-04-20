---
name: trend-analysis
description: Query and interpret Google Trends data through sellerclaw-api for demand validation, niche discovery, and seasonal pattern detection.
---

# Trend Analysis Skill

## Goal

Reference guide for `sellerclaw-api` endpoints used to query demand and media signals:
legacy **Google Trends** routes plus optional **DataForSEO** JSON APIs for higher-precision
metrics (keyword trends graph, content sentiment). Use DataForSEO when configured; otherwise
use Google Trends as documented below.

## Base URL and authentication

- Base URL: `{{api_base_url}}`
- Auth header: `Authorization: Bearer $AGENT_API_KEY`
- Do not print token values in logs or messages.

## Conventions

- Use `exec curl` for HTTP requests.
- All request/response bodies are JSON.
- Google Trends and DataForSEO are configured server-side (user or corporate credentials).
  If a provider is unavailable, endpoints may return `503` or an error body — report clearly
  and use browser-based validation if allowed.

## Response shapes (high-level)

`InterestOverTimeResponse`

| Field | Type | Notes |
|---|---|---|
| keywords | string[] | Parsed search terms |
| series | KeywordSeries[] | One series per keyword |
| provider | string | Backend provider id |

`KeywordSeries`

| Field | Type | Notes |
|---|---|---|
| keyword | string | Search term |
| average | int \| null | Mean interest (0–100 scale) |
| points | InterestPointSchema[] | Time series |

`InterestPointSchema`

| Field | Type |
|---|---|
| date | string |
| timestamp | int |
| value | int (0–100) |

`RelatedQueriesResponse`

| Field | Type |
|---|---|
| keyword | string |
| top | RelatedQuerySchema[] |
| rising | RelatedQuerySchema[] |
| provider | string |

`RelatedQuerySchema`

| Field | Type | Notes |
|---|---|---|
| query | string | Related search term |
| value | int | Relative interest weight |
| link | string \| null | |
| type | string | |

`TrendingSearchesResponse`

| Field | Type |
|---|---|
| geo | string |
| searches | TrendingSearchSchema[] |
| provider | string |

`TrendingSearchSchema`

| Field | Type | Notes |
|---|---|---|
| query | string | |
| search_volume | int \| null | When provider supplies it |
| categories | string[] | |
| related_queries | string[] | |

## DataForSEO (POST JSON, when integration active)

Base path: `{{api_base_url}}/research/seo/...` — all bodies are JSON objects; responses include
`cost_usd` from the provider.

| Endpoint | Purpose |
|---|---|
| `POST .../keyword-trends` | Google Trends Explore (graph points, relative 0–100) |
| `POST .../content-sentiment` | News/social sentiment buckets for a keyword or text sample |

Example:

```bash
curl -s -X POST "{{api_base_url}}/research/seo/keyword-trends" \
  -H "Authorization: Bearer $AGENT_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"keywords":["dog leash"],"location_code":2840,"language_code":"en"}'
```

## Endpoints (Google Trends)

### GET /research/trends/interest-over-time

Interest over time for one or more search terms (max 5 keywords per request).

Query params:

| Name | Type | Required | Default | Notes |
|---|---|---|---|---|
| keywords | string | yes | | Comma-separated |
| timeframe | string | no | today 12-m | See timeframe format below |
| geo | string | no | | Region code when applicable |
| category | int | no | 0 | |

Response: `InterestOverTimeResponse`

Example:

```bash
curl -s "{{api_base_url}}/research/trends/interest-over-time?keywords=dog+leash,cat+harness&timeframe=today+12-m" \
  -H "Authorization: Bearer $AGENT_API_KEY"
```

### GET /research/trends/related-queries

Related and rising queries for one keyword.

Query params:

| Name | Type | Required | Default |
|---|---|---|---|
| keyword | string | yes | |
| timeframe | string | no | today 12-m |
| geo | string | no | |
| category | int | no | 0 |

Response: `RelatedQueriesResponse`

Example:

```bash
curl -s "{{api_base_url}}/research/trends/related-queries?keyword=dog+leash&timeframe=today+12-m" \
  -H "Authorization: Bearer $AGENT_API_KEY"
```

### GET /research/trends/trending

Currently trending searches.

Query params:

| Name | Type | Required | Default | Notes |
|---|---|---|---|---|
| geo | string | no | US | |
| hours | int | no | 24 | One of 4, 24, 48, 168 |
| category | string | no | | |

Response: `TrendingSearchesResponse`

Example:

```bash
curl -s "{{api_base_url}}/research/trends/trending?geo=US&hours=24" \
  -H "Authorization: Bearer $AGENT_API_KEY"
```

### Other useful endpoints

- `GET /research/trends/interest-by-region` — geographic demand for one keyword.
- `GET /research/trends/compare` — compare 2–5 keywords side by side (requires ≥2 keywords).
- `GET /research/trends/related-topics` — related topics (top / rising).

## Timeframe format

- `today 12-m` — past 12 months
- `today 3-m` — past 3 months
- `today 1-m` — past month
- `today 7-d` — past 7 days
- Custom range: `YYYY-MM-DD YYYY-MM-DD`

## Interpretation guidelines

### Trend direction (from interest-over-time)

- **Growing**: recent window average clearly above earlier window (e.g. last 3 months vs prior 9 months) by ≥10% on the 0–100 scale.
- **Stable**: change within ±10%.
- **Declining**: recent window below earlier window by ≥10%.
- **Seasonal**: repeating peaks aligned with holidays or seasons.

### Signal interpretation

| Signal | Type | Meaning |
|--------|------|---------|
| Spike + crash in 1–2 months | Red | Likely fad |
| Flat at low values for whole series | Red | Thin/noisy signal |
| Related queries mostly brand names | Red | Demand captured by incumbents |
| Steady upward 6+ months | Green | Growing demand |
| Multiple distinct rising queries | Green | Broadening interest |
| Trends align with supplier availability | Green | Actionable supply |

## Progress checkpoints

If the task includes an `agent_task_id`, report progress after collecting trend data
for all requested keywords via `POST /goals/agent-tasks/{agent_task_id}/progress`.
Include the trend direction and key data points per keyword so results survive session
timeouts.

## Scope limits by effort

Read the effort level from the Agent Task instructions (`Effort: QUICK/STANDARD/DEEP`).
If not stated, use Standard.

| Limit | Quick | Standard | Deep |
|-------|-------|----------|------|
| Trend queries | 1 (12-month only) | 2-3 (12-month + 5-year) | 5+ (multiple timeframes, regions) |
| Keyword variations | 1-2 seeds | 3-5 seeds | 5-10 seeds + long-tail |
| DataForSEO calls (if available) | 1 | 2-3 | 5-8 |
| Cross-referencing | No | No | Yes — query both Trends API and DataForSEO, report both |
| Related queries depth | Skip | Top only | Top + rising, analyze patterns |

## Fallback when Trends API is unavailable

If `/research/trends/*` returns 503 or errors:

1. Use `web_search`: "{keywords} google trends 2025 2026" — extract trend
   direction from SEO articles that reference Google Trends data.
2. Use `web_search`: "{keywords} search trend growing declining" — find
   market analyses with trend assessments.
3. Browser (if available): navigate to trends.google.com, enter keywords,
   visually assess the graph, report direction and approximate growth rate.

Always note in results which source was used for trend data.

### Search volume fallback (when DataForSEO is unavailable)

Absolute search volume requires DataForSEO `keyword-volume`. When unavailable:

1. Use `web_search`: "{keywords} monthly search volume" — SEO tool screenshots
   and blog posts often cite volume numbers.
2. Use `web_search`: "{keywords} market size 2026" — market reports give demand signals.
3. Use Google Trends relative interest (0-100 scale) as a proxy — note that this is
   relative, not absolute.

Report `search_volume_source` as `"web_search_estimate"` or `"unavailable"` accordingly.

## Guardrails

- Scope limits are effort-dependent — see the "Scope limits by effort" section above.
- Always include timeframe and geo (when used) in results so the supervisor knows the scope.
- If an endpoint errors or returns empty data, state that explicitly and suggest marketplace
  or browser validation when mode allows.
- Do not over-interpret small movements — values are relative, not absolute search volume.
