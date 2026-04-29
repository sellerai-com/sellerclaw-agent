---
name: trend-analysis
description: "Measure demand direction and seasonality for a keyword or niche via Google Trends (and DataForSEO when configured) through the `sellerclaw` CLI. Use when the user asks whether a niche is rising or declining, has seasonal peaks, or needs demand validation before sourcing or ad spend. For raw search volumes use `keyword-research`."
---

# Trend Analysis Skill

## Goal

Reference guide for `sellerclaw` CLI commands used to query demand and media signals: legacy **Google Trends** routes plus optional **DataForSEO** (keyword trends graph, content sentiment) for higher-precision metrics. Use DataForSEO when configured; otherwise use Google Trends as documented below.

JSON on stdout; structured errors on stderr (exit 1=user/api, 2=server/network, 3=auth). If a provider is unavailable, the CLI surfaces an error / 503 — report clearly and use browser-based validation if allowed.

## Google Trends commands

| Command | Purpose |
|---|---|
| `sellerclaw research-trends get-interest-over-time --keywords k1,k2 [--timeframe …] [--geo …] [--category 0]` | Interest over time, max 5 keywords (`--keywords` plural, comma-separated) |
| `sellerclaw research-trends get-related-queries --keyword <kw> [--timeframe …] [--geo …]` | Related + rising queries for one keyword (`--keyword` singular) |
| `sellerclaw research-trends get-trending-searches [--geo US] [--hours 24] [--category …]` | Currently trending searches (`hours` ∈ {4, 24, 48, 168}) |
| `sellerclaw research-trends get-interest-by-region --keyword <kw> [--timeframe …] [--geo …] [--resolution …]` | Geographic demand for one keyword |
| `sellerclaw research-trends compare-keywords --keywords k1,k2 [--timeframe …] [--geo …]` | Compare 2–5 keywords side by side |
| `sellerclaw research-trends get-related-topics --keyword <kw> [--timeframe …] [--geo …]` | Related topics (top / rising) |

Examples:

```bash
sellerclaw research-trends get-interest-over-time --keywords "dog leash,cat harness" --timeframe "today 12-m"
sellerclaw research-trends get-related-queries --keyword "dog leash" --timeframe "today 12-m"
sellerclaw research-trends get-trending-searches --geo US --hours 24
```

## DataForSEO commands (when `research_seo` is active)

| Command | Purpose |
|---|---|
| `sellerclaw research-seo post-keyword-trends -b '<json>'` | Google Trends Explore (graph points, relative 0–100) |
| `sellerclaw research-seo post-content-sentiment -b '<json>'` | News/social sentiment buckets for a keyword or text sample |

Body schemas: `sellerclaw describe <op_id>`. Responses include `cost_usd`.

Example:

```bash
sellerclaw research-seo post-keyword-trends -b '{
  "keywords": ["dog leash"],
  "location_code": 2840,
  "language_code": "en"
}'
```

## Timeframe format

- `today 12-m` — past 12 months
- `today 3-m` — past 3 months
- `today 1-m` — past month
- `today 7-d` — past 7 days
- Custom range: `YYYY-MM-DD YYYY-MM-DD`

## Interpretation guidelines

### Trend direction (from `get-interest-over-time`)

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

If the task includes an `agent_task_id`, report progress after collecting trend data for all requested keywords via `sellerclaw agent-goals add-progress-note <task_id> -b '{"message":"…"}'`. Include the trend direction and key data points per keyword so results survive session timeouts.

## Scope limits by effort

Read the effort level from the Agent Task instructions (`Effort: QUICK/STANDARD/DEEP`). If not stated, use Standard.

| Limit | Quick | Standard | Deep |
|-------|-------|----------|------|
| Trend queries | 1 (12-month only) | 2-3 (12-month + 5-year) | 5+ (multiple timeframes, regions) |
| Keyword variations | 1-2 seeds | 3-5 seeds | 5-10 seeds + long-tail |
| DataForSEO calls (if available) | 1 | 2-3 | 5-8 |
| Cross-referencing | No | No | Yes — query both Trends and DataForSEO, report both |
| Related queries depth | Skip | Top only | Top + rising, analyze patterns |

## Fallback when Trends commands are unavailable

If `sellerclaw research-trends …` exits with `503` or errors:

1. Use `web_search`: "{keywords} google trends 2025 2026" — extract trend direction from SEO articles that reference Google Trends data.
2. Use `web_search`: "{keywords} search trend growing declining" — find market analyses with trend assessments.
3. Browser (if available): navigate to trends.google.com, enter keywords, visually assess the graph, report direction and approximate growth rate.

Always note in results which source was used for trend data.

### Search volume fallback (when DataForSEO is unavailable)

Absolute search volume requires `sellerclaw research-seo post-keyword-volume`. When unavailable:

1. Use `web_search`: "{keywords} monthly search volume" — SEO tool screenshots and blog posts often cite volume numbers.
2. Use `web_search`: "{keywords} market size 2026" — market reports give demand signals.
3. Use Google Trends relative interest (0-100 scale) as a proxy — note that this is relative, not absolute.

Report `search_volume_source` as `"web_search_estimate"` or `"unavailable"` accordingly.

## Guardrails

- Scope limits are effort-dependent — see the "Scope limits by effort" section above.
- Always include timeframe and geo (when used) in results so the supervisor knows the scope.
- If a CLI command errors or returns empty data, state that explicitly and suggest marketplace or browser validation when mode allows.
- Do not over-interpret small movements — values are relative, not absolute search volume.

## Reference

- **Full response schemas** (`InterestOverTimeResponse`, `RelatedQueriesResponse`, `TrendingSearchesResponse`, …): `references/data-model.md`.
- **OpenAPI source of truth:** `sellerclaw describe <operation_id>`; discover ops via `sellerclaw list-operations --tag research-trends` or `--tag research-seo`.
