# Trend Analysis — response schemas (`sellerclaw research-trends` / `research-seo`)

Schemas come from the OpenAPI bundle inside `sellerclaw` (`sellerclaw describe <operation_id>`). Below are the fields the agent should rely on. Provider field always echoes the backend (e.g. `pytrends`, `dataforseo`).

---

## `get-interest-over-time` → `InterestOverTimeResponse`

| Field | Type | Notes |
|---|---|---|
| `keywords` | string[] | Parsed search terms (echo of input) |
| `series` | KeywordSeries[] | One series per keyword |
| `provider` | string | Backend provider id |

### `KeywordSeries`

| Field | Type | Notes |
|---|---|---|
| `keyword` | string | Search term |
| `average` | int \| null | Mean interest on the 0–100 scale |
| `points` | InterestPointSchema[] | Time series |

### `InterestPointSchema`

| Field | Type |
|---|---|
| `date` | string |
| `timestamp` | int |
| `value` | int (0–100) |

---

## `get-related-queries` → `RelatedQueriesResponse`

| Field | Type | Notes |
|---|---|---|
| `keyword` | string | Echo of `--keyword` |
| `top` | RelatedQuerySchema[] | Top related queries |
| `rising` | RelatedQuerySchema[] | Rising queries (use to spot momentum) |
| `provider` | string | |

### `RelatedQuerySchema`

| Field | Type | Notes |
|---|---|---|
| `query` | string | Related search term |
| `value` | int | Relative interest weight |
| `link` | string \| null | Optional link to Trends Explore |
| `type` | string | e.g. `top` / `rising` |

---

## `get-related-topics` → `RelatedTopicsResponse`

Same shape as `RelatedQueriesResponse` but `top` / `rising` carry topic objects (with `mid`, `title`, `type`) instead of plain query strings. Use to broaden semantic context around a keyword.

---

## `get-interest-by-region` → `InterestByRegionResponse`

| Field | Type | Notes |
|---|---|---|
| `keyword` | string | Echo of `--keyword` |
| `resolution` | string | `country`, `region`, `city`, `dma` (depends on `--resolution`) |
| `regions` | RegionInterest[] | Per-region rows |
| `provider` | string | |

### `RegionInterest`

| Field | Type | Notes |
|---|---|---|
| `region` | string | Region name |
| `geo_code` | string | ISO / Trends region code |
| `value` | int (0–100) | Relative interest |

---

## `get-trending-searches` → `TrendingSearchesResponse`

| Field | Type | Notes |
|---|---|---|
| `geo` | string | Echo of `--geo` |
| `searches` | TrendingSearchSchema[] | |
| `provider` | string | |

### `TrendingSearchSchema`

| Field | Type | Notes |
|---|---|---|
| `query` | string | |
| `search_volume` | int \| null | When provider supplies it |
| `categories` | string[] | |
| `related_queries` | string[] | |

---

## `compare-keywords` → `CompareKeywordsResponse`

Same shape as `InterestOverTimeResponse` but `series` length matches the requested 2–5 keywords. Use the per-keyword `average` to rank relative interest at a glance.

---

## DataForSEO — `post-keyword-trends` (`/research/seo/keyword-trends`)

Body (required): `keywords` (list[string]). Optional: `location_code` (e.g. `2840` = US), `language_code` (e.g. `en`), `timeframe`, `category`.

Response includes:

| Field | Type | Notes |
|---|---|---|
| `provider` | string | `dataforseo` |
| `cost_usd` | float | Vendor cost for the call |
| `response` | object | Raw DataForSEO payload — graph points (relative 0–100), location/language echo, monthly buckets |

Read `response.tasks[*].result[*].items` for the time-series data; structure mirrors DataForSEO's Keyword Data API.

---

## DataForSEO — `post-content-sentiment`

Body (required): `keyword` *or* a text sample. Optional: `location_code`, `language_code`.

Response:

| Field | Type | Notes |
|---|---|---|
| `provider` | string | |
| `cost_usd` | float | |
| `response` | object | Raw payload — sentiment buckets (`positive`, `negative`, `neutral`) + sample articles/posts |

Use sentiment percentages for risk signals; do not overweight on small sample sizes (`response.tasks[*].result[*].items_count` < ~20).
