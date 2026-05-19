# Facebook Ads — data models (`sellerclaw facebook-ads`)

Schemas come from the OpenAPI bundle inside `sellerclaw` (`sellerclaw describe <operation_id>`). The proxy passes through Facebook Marketing API responses, so server schemas are open (`additionalProperties: true`); the fields below are the ones the agent should rely on.

**Rule:** required vs optional keys are listed only under request bodies. Response sections describe **meaning**, not JSON Schema minutiae.

Money values are floats in the ad account currency (usually USD). Dates are `YYYY-MM-DD`.

---

## CampaignSchema (`get-campaigns`, `get-campaign`, `create-campaign`, `patch-campaign`)

| Field | Type | Notes |
|---|---|---|
| `id` | string | Facebook campaign ID |
| `name` | string | |
| `status` | string | `ACTIVE`, `PAUSED`, `ARCHIVED` |
| `objective` | string | `CONVERSIONS`, `CATALOG_SALES`, `TRAFFIC`, `REACH`, … |
| `daily_budget` | float \| null | Daily budget in account currency |
| `lifetime_budget` | float \| null | Lifetime budget |
| `created_at` | string | ISO datetime |

### `create-campaign` body — `CreateCampaignRequest`

- **`name`** (required) — campaign label.
- **`objective`** (required) — Facebook objective string.
- **`daily_budget`** *or* **`lifetime_budget`** — set exactly one.
- **`status`** — defaults to `PAUSED`; never set `ACTIVE` on create.

### `patch-campaign` body

Partial campaign fields you want to change: `name`, `status`, `daily_budget`, `lifetime_budget`. Server caps budget delta at ±20%.

---

## AdSetSchema (`get-campaign-adsets`, `create-adset`, `patch-adset`)

| Field | Type | Notes |
|---|---|---|
| `id` | string | |
| `campaign_id` | string | Parent campaign |
| `name` | string | |
| `status` | string | `ACTIVE`, `PAUSED` |
| `daily_budget` | float \| null | |
| `bid_strategy` | string | `lowest_cost`, `cost_cap`, `bid_cap` |
| `bid_amount` | float \| null | Required when bid strategy is `cost_cap` / `bid_cap` |
| `optimization_goal` | string | `CONVERSIONS`, `LINK_CLICKS`, `LANDING_PAGE_VIEWS`, … |
| `targeting` | object | See **Targeting** below |
| `start_time` | string \| null | ISO datetime |
| `end_time` | string \| null | ISO datetime |

### `create-adset` body — `CreateAdSetRequest`

Required: **`campaign_id`**, **`name`**, **`daily_budget`**, **`bid_strategy`**, **`optimization_goal`**, **`targeting`**.
Optional: `bid_amount`, `start_time`, `end_time`, `status` (defaults `PAUSED`).

### Targeting object

```json
{
  "age_min": 25,
  "age_max": 55,
  "genders": [0],
  "countries": ["US"],
  "interests": [{"id": "6003139266461", "name": "Fitness"}],
  "custom_audiences": [],
  "lookalike_audiences": [],
  "placements": "automatic"
}
```

- **`age_min` / `age_max`** — integers, Facebook range 13–65.
- **`genders`** — `[0]` all, `[1]` male, `[2]` female.
- **`countries`** — array of ISO-2 codes.
- **`interests`** — objects with `id` from `search-interests`, `name` is informational.
- **`custom_audiences`**, **`lookalike_audiences`** — arrays of audience IDs from `list-audiences`.
- **`placements`** — `automatic` or specific placement keys (Facebook docs).

### `duplicate-adset` body — `DuplicateAdSetRequest`

All optional: `name`, `campaign_id` (default = source campaign), `daily_budget`. Returns a new ad set in `PAUSED`.

---

## AdSchema (`create`, `patch`)

| Field | Type | Notes |
|---|---|---|
| `id` | string | |
| `ad_set_id` | string | |
| `name` | string | |
| `status` | string | `ACTIVE`, `PAUSED` |
| `creative` | AdCreativeSchema | |

### `create` body — `CreateAdRequest`

Required: **`ad_set_id`**, **`name`**, **`creative`**.
Optional: `status` (defaults `PAUSED`).

### AdCreativeSchema

| Field | Required | Type | Notes |
|---|---|---|---|
| `id` | — | string | Set after creation |
| `title` | yes | string | Headline; ≤40 chars recommended |
| `body` | yes | string | Primary text; ≤125 chars before "See more" |
| `image_url` | one of | string \| null | HTTPS; 1080×1080 (1:1) or 1080×1350 (4:5) min |
| `video_url` | one of | string \| null | HTTPS; MP4/MOV; ≤4 GB; ≤15 min |
| `call_to_action` | yes | string | Enum: `SHOP_NOW`, `LEARN_MORE`, `SIGN_UP`, `SUBSCRIBE`, `DOWNLOAD`, … |
| `link_url` | yes | string | HTTPS destination |

Either `image_url` or `video_url` is required (exactly one is the usual case). `list-creatives` returns these fields (subset) for analysis and creative-refresh planning.

---

## `upload-image`

Multipart upload via the CLI; field name `image`. Response is the raw Facebook `adimages` payload — keep `hash` or `url` to reference inside the next `create` body.

---

## MetricsSchema (`get-metrics`)

Query params:

- **`level`** (required) — `campaign`, `adset`, or `ad`.
- **`ids`** — comma-separated entity IDs; default = all active.
- **`date_from`**, **`date_to`** — defaults to last 7 days through today.
- **`breakdown`** — `none` (default), `day`, `age`, `gender`, `country`, `placement`.

Per-row metric fields:

| Field | Type | Notes |
|---|---|---|
| `spend` | float | |
| `impressions` | int | |
| `clicks` | int | Link clicks |
| `ctr` | float | Click-through rate (%) |
| `cpc` | float | Cost per click |
| `conversions` | int | Purchase conversions |
| `cpa` | float | Cost per acquisition |
| `roas` | float | Return on ad spend |
| `cpm` | float | Cost per 1000 impressions |
| `frequency` | float | Avg times shown per person |
| `reach` | int | Unique people reached |

Response shape: `{ "items": [{ "id", "name", "date", "metrics": MetricsSchema }] }`.

---

## AudienceSchema (`list-audiences`, `create-lookalike-audience`)

| Field | Type | Notes |
|---|---|---|
| `id` | string | |
| `name` | string | |
| `type` | string | `custom`, `lookalike`, `saved` |
| `size` | int \| null | Estimated audience size |
| `source` | string \| null | Origin (pixel, customer_list, …) |

### `create-lookalike-audience` body — `CreateLookalikeAudienceRequest`

Required: **`name`**, **`source_audience_id`**, **`country`** (2-letter ISO), **`ratio`** (0.01–0.20). Smaller ratios are tighter lookalikes.

---

## Targeting search

- `search-interests --q <text>` — returns interest objects with `id`, `name`, `audience_size`, `path`. Pick `id` for the `targeting.interests[]` payload.
- `search-locations --q <text>` — returns geo objects with `key`, `name`, `type`. The `key` is what Facebook stores; `countries[]` in targeting still uses ISO-2.

---

## Action log (`get-action-log`)

Each row:

| Field | Type | Notes |
|---|---|---|
| `id` | UUID | Internal log id |
| `user_id` | UUID | Owner |
| `platform` | string | `facebook` here |
| `action_type` | enum | `create_campaign`, `update_campaign`, `pause_campaign`, `create_adset`, `update_adset`, `update_budget`, `create_ad`, `update_ad`, `upload_image`, `duplicate_adset`, `create_audience` |
| `entity_id` | string | Facebook entity touched |
| `entity_type` | string | e.g. `campaign`, `adset` |
| `details` | object | Action-specific payload (deltas etc.) |
| `created_at` | datetime | ISO |

Use it before scaling: skip a budget bump if `update_budget` for the entity appeared within the last 3 days.
