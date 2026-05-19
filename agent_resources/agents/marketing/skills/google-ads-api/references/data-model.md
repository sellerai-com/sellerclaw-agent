# Google Ads — data models (`sellerclaw google-ads`)

Schemas come from the OpenAPI bundle inside `sellerclaw` (`sellerclaw describe <operation_id>`). The proxy passes through Google Ads / Merchant Center responses, so server schemas are open (`additionalProperties: true`); the fields below are the ones the agent should rely on.

**Rule:** required vs optional keys are listed only under request bodies. Response sections describe **meaning**, not JSON Schema minutiae.

Money values are floats in account currency. Dates are `YYYY-MM-DD`.

---

## GoogleCampaignSchema (`get-campaigns`, `get-campaign`, `create-campaign`, `patch-campaign`)

| Field | Type | Notes |
|---|---|---|
| `id` | string | Google campaign ID |
| `name` | string | |
| `status` | string | `ENABLED`, `PAUSED`, `REMOVED` |
| `type` | string | `SHOPPING`, `PERFORMANCE_MAX`, `SEARCH`, … |
| `bidding_strategy` | string | `MAXIMIZE_CONVERSIONS`, `TARGET_ROAS`, `MAXIMIZE_CONVERSION_VALUE`, … |
| `target_roas` | float \| null | Target ROAS value (e.g. `4.0` = 400%) |
| `daily_budget` | float | Daily budget in account currency |
| `budget_resource_name` | string | Internal Google budget resource path |
| `warning` | string \| null | Optional warning (e.g. PMax learning period) |

### `create-campaign` body — `CreateGoogleCampaignRequest`

Required: **`name`**, **`type`**, **`daily_budget`** (>0), **`bidding_strategy`**.

Optional:

- **`target_roas`** — only relevant for ROAS-based bidding strategies.
- **`merchant_id`** — Shopping only; usually inferred from connected credentials.
- **`campaign_priority`** — Shopping only; integer 0–2.
- **`status`** — server forces `PAUSED` regardless of input.
- **`asset_group`** — PMax only; see below.

### Asset group payload (PMax only) — `CreateGoogleCampaignAssetGroupRequest`

```json
{
  "name": "Main Asset Group",
  "final_url": "https://store.example.com",
  "headlines": ["..."],
  "descriptions": ["..."],
  "image_urls": ["https://..."],
  "logo_urls": ["https://..."]
}
```

Schema-level required: **`name`**, **`final_url`**.

**Agent-enforced minimums** (the proxy accepts under-spec input and produces a broken campaign — always validate before sending):

| Field | Min | Max | Per-item limit |
|---|---|---|---|
| `headlines` | 5 | 15 | ≤30 chars, unique |
| `descriptions` | 3 | 5 | ≤90 chars (one ≤60 recommended) |
| `image_urls` | 2 | 20 | ≥1 landscape (1.91:1) + ≥1 square (1:1), HTTPS |
| `logo_urls` | 1 | 5 | ≥1 square (1:1, 1200×1200), HTTPS |
| `name` | 1 | 1 | ≤80 chars; keep ≤25 if used as business name |
| `final_url` | 1 | 1 | HTTPS, must match advertised domain |

### `patch-campaign` body — `PatchGoogleCampaignRequest`

Partial fields you want to change: `name`, `status`, `daily_budget` (server caps delta at ±20%), `bidding_strategy`, `target_roas`.

---

## GoogleAdGroupSchema (`get-campaign-groups`, `create-group`, `patch-group`)

| Field | Type | Notes |
|---|---|---|
| `id` | string | |
| `campaign_id` | string | Parent campaign |
| `name` | string | |
| `status` | string | `ENABLED`, `PAUSED` |
| `cpc_bid` | float \| null | Max CPC bid |

### `create-group` body — `CreateGoogleAdGroupRequest`

Required: **`campaign_id`**, **`name`**.
Optional: `cpc_bid` (>0). `status` is ignored — server forces `PAUSED`.

### `patch-group` body

Partial: `name`, `status`, `cpc_bid`.

---

## GoogleAssetGroupSchema (`get-asset-groups`, `patch-asset-group`)

| Field | Type | Notes |
|---|---|---|
| `id` | string | |
| `campaign_id` | string | |
| `name` | string | |
| `status` | string | `ENABLED`, `PAUSED` |
| `resource_name` | string | Google resource path |

### `patch-asset-group` body

Partial: `name`, `status` (and any extra creative fields the server accepts — describe op for the current shape).

---

## GoogleMetricsSchema (`get-metrics`)

Query params:

- **`level`** — `campaign` (default), `ad_group`, `product_group`.
- **`ids`** — comma-separated entity IDs; default = all.
- **`date_from`**, **`date_to`** — defaults to last 7 days through today.
- **`breakdown`** — `none` (default), `day`, `device`.

Per-row metric fields:

| Field | Type | Notes |
|---|---|---|
| `spend` | float | Cost in account currency |
| `impressions` | int | |
| `clicks` | int | |
| `ctr` | float | % |
| `avg_cpc` | float | Average CPC |
| `conversions` | float | Can be fractional (Google attribution) |
| `conversion_value` | float | Revenue attributed |
| `cpa` | float | Cost per conversion |
| `roas` | float | `conversion_value / spend` |

Response shape: `{ "items": [{ "id", "name", "date", "metrics": GoogleMetricsSchema }] }`.

---

## MerchantProductSchema (`get-products`)

| Field | Type | Notes |
|---|---|---|
| `product_id` | string | Merchant Center product id |
| `offer_id` | string | Merchant offer id |
| `title` | string | |
| `channel` | string | `online` / `local` |
| `availability` | string | `in stock` / `out of stock` / `preorder` / `backorder` |
| `status` | string | Destination statuses summary |
| `issues` | list | Item-level issues / disapprovals |

Use to verify Shopping inventory before launching `SHOPPING` or PMax campaigns.

---

## Keyword ideas (`post-keyword-ideas`) — `GoogleKeywordIdeasRequest`

Body:

- **`keywords`** (required, list[string]) — seed terms.
- **`language`** (optional, string) — Google language constant or ISO code accepted by the proxy.
- **`geo_target_constants`** (optional, list[string]) — geo target resource names.

Response: passthrough Google `KeywordPlanIdeaService` payload — read `keyword_idea_metrics` for `avg_monthly_searches`, `competition`, `low_top_of_page_bid_micros`, `high_top_of_page_bid_micros`.

---

## Recommendations (`get-recommendations`)

Passthrough of Google `RecommendationService` results. Each entry exposes `type` (e.g. `KEYWORD`, `MAXIMIZE_CONVERSIONS_OPT_IN`), `impact` (estimated metric uplift), and a recommendation-specific payload. Treat as advisory — present to supervisor before applying.

---

## Action log (`get-action-log`)

Each row:

| Field | Type | Notes |
|---|---|---|
| `id` | UUID | Internal log id |
| `user_id` | UUID | Owner |
| `platform` | string | `google` here |
| `action_type` | enum | `create_campaign`, `update_campaign`, `pause_campaign`, `create_adset`, `update_adset`, `update_budget`, `generate_keyword_ideas`, `create_audience`, … (shared enum across platforms — Google subset is a slice) |
| `entity_id` | string | Google entity touched |
| `entity_type` | string | e.g. `campaign`, `ad_group`, `asset_group` |
| `details` | object | Action-specific payload (deltas etc.) |
| `created_at` | datetime | ISO |

Use it before scaling: skip a budget bump if `update_budget` for the entity appeared within the last 3 days.
