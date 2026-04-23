---
name: facebook-ads-api
description: "Work with Facebook / Meta Ads through sellerclaw-api endpoints for campaigns, ad sets, ads, audiences, and performance metrics."
---

# Facebook Ads API Skill

## Goal
Reference guide for `sellerclaw-api` endpoints used to manage Facebook / Meta advertising campaigns.

## Base URL and Authentication
- Base URL: `{{api_base_url}}`
- Auth header: `Authorization: Bearer $AGENT_API_KEY`
- Do not print token values in logs or messages.

## Conventions
- Use `exec curl` for HTTP requests.
- All request/response bodies are JSON.
- Ad account ID is resolved server-side from the connected integration — you do not need to pass it.
- Monetary values are in the ad account's currency (usually USD), as floats.
- Date format: `YYYY-MM-DD`.

## Schemas (high-level)

`CampaignSchema`
| Field | Type | Notes |
|---|---|---|
| id | string | Facebook campaign ID |
| name | string | |
| status | string | ACTIVE, PAUSED, ARCHIVED |
| objective | string | CONVERSIONS, CATALOG_SALES, TRAFFIC, etc. |
| daily_budget | float\|null | Daily budget in account currency |
| lifetime_budget | float\|null | Lifetime budget |
| created_at | string | ISO datetime |

`AdSetSchema`
| Field | Type | Notes |
|---|---|---|
| id | string | |
| campaign_id | string | |
| name | string | |
| status | string | ACTIVE, PAUSED |
| daily_budget | float\|null | |
| bid_strategy | string | lowest_cost, cost_cap, bid_cap |
| bid_amount | float\|null | For cost_cap/bid_cap |
| targeting | object | Audience targeting config |
| optimization_goal | string | CONVERSIONS, LINK_CLICKS, etc. |
| start_time | string\|null | ISO datetime |
| end_time | string\|null | ISO datetime |

`AdSchema`
| Field | Type | Notes |
|---|---|---|
| id | string | |
| ad_set_id | string | |
| name | string | |
| status | string | ACTIVE, PAUSED |
| creative | AdCreativeSchema | |

`AdCreativeSchema`
| Field | Type | Notes |
|---|---|---|
| id | string | |
| title | string | Headline |
| body | string | Primary text |
| image_url | string\|null | |
| video_url | string\|null | |
| call_to_action | string | SHOP_NOW, LEARN_MORE, etc. |
| link_url | string | Destination URL |

`MetricsSchema`
| Field | Type | Notes |
|---|---|---|
| spend | float | |
| impressions | int | |
| clicks | int | Link clicks |
| ctr | float | Click-through rate (%) |
| cpc | float | Cost per click |
| conversions | int | Purchase conversions |
| cpa | float | Cost per acquisition |
| roas | float | Return on ad spend |
| cpm | float | Cost per 1000 impressions |
| frequency | float | Avg times shown per person |
| reach | int | Unique people reached |

`AudienceSchema`
| Field | Type | Notes |
|---|---|---|
| id | string | |
| name | string | |
| type | string | custom, lookalike, saved |
| size | int\|null | Estimated audience size |
| source | string\|null | Origin (pixel, customer_list, etc.) |

## Endpoints

### GET /ads/facebook/campaigns
List campaigns with optional status filter.

Query params:
| Name | Type | Required | Default |
|---|---|---|---|
| status | string | no | all |
| limit | int | no | 50 |

Response: `{ "items": CampaignSchema[] }`

Example:
```bash
curl -s "{{api_base_url}}/ads/facebook/campaigns?status=ACTIVE" \
  -H "Authorization: Bearer $AGENT_API_KEY"
```

### GET /ads/facebook/campaigns/{campaign_id}
Get campaign details.

Response: `CampaignSchema`

### POST /ads/facebook/campaigns
Create a new campaign.

Body:
| Field | Type | Required |
|---|---|---|
| name | string | yes |
| objective | string | yes |
| daily_budget | float | yes (or lifetime_budget) |
| lifetime_budget | float | no |
| status | string | no (default: PAUSED) |

Response: `CampaignSchema`

Important: campaigns are created as PAUSED by default. Activate after supervisor approval.

Example:
```bash
curl -s -X POST "{{api_base_url}}/ads/facebook/campaigns" \
  -H "Authorization: Bearer $AGENT_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Summer Sale - Wireless Earbuds",
    "objective": "CONVERSIONS",
    "daily_budget": 50.0,
    "status": "PAUSED"
  }'
```

### PATCH /ads/facebook/campaigns/{campaign_id}
Update campaign (status, budget, name).

Body: partial `CampaignSchema` (only fields to update).

Response: `CampaignSchema`

### GET /ads/facebook/campaigns/{campaign_id}/adsets
List ad sets for a campaign.

Query params:
| Name | Type | Required | Default |
|---|---|---|---|
| status | string | no | all |

Response: `{ "items": AdSetSchema[] }`

### POST /ads/facebook/adsets
Create an ad set.

Body:
| Field | Type | Required |
|---|---|---|
| campaign_id | string | yes |
| name | string | yes |
| daily_budget | float | yes |
| bid_strategy | string | yes |
| bid_amount | float | no (required for cost_cap/bid_cap) |
| optimization_goal | string | yes |
| targeting | object | yes |
| start_time | string | no |
| end_time | string | no |
| status | string | no (default: PAUSED) |

Targeting object:
```json
{
  "age_min": 25,
  "age_max": 55,
  "genders": [0],
  "countries": ["US"],
  "interests": [
    {"id": "6003139266461", "name": "Fitness"}
  ],
  "custom_audiences": [],
  "lookalike_audiences": [],
  "placements": "automatic"
}
```

Response: `AdSetSchema`

Example:
```bash
curl -s -X POST "{{api_base_url}}/ads/facebook/adsets" \
  -H "Authorization: Bearer $AGENT_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "campaign_id": "120210123456",
    "name": "US 25-55 Fitness Interest",
    "daily_budget": 25.0,
    "bid_strategy": "lowest_cost",
    "optimization_goal": "CONVERSIONS",
    "targeting": {
      "age_min": 25,
      "age_max": 55,
      "countries": ["US"],
      "interests": [{"id": "6003139266461", "name": "Fitness"}],
      "placements": "automatic"
    }
  }'
```

### PATCH /ads/facebook/adsets/{adset_id}
Update ad set (status, budget, bid, targeting).

Body: partial update fields.

Response: `AdSetSchema`

### POST /ads/facebook/adsets/{adset_id}/duplicate
Duplicate an ad set (for A/B testing or scaling).

Body:
| Field | Type | Required |
|---|---|---|
| name | string | no (auto-generated if omitted) |
| campaign_id | string | no (same campaign if omitted) |
| daily_budget | float | no (same as source) |

Response: `AdSetSchema` (the new duplicate)

### POST /ads/facebook/ads
Create an ad within an ad set.

Body:
| Field | Type | Required |
|---|---|---|
| ad_set_id | string | yes |
| name | string | yes |
| creative | object | yes |
| status | string | no (default: PAUSED) |

Creative object:
```json
{
  "title": "50% Off Wireless Earbuds",
  "body": "Premium sound. All-day comfort. Free shipping.",
  "image_url": "https://...",
  "call_to_action": "SHOP_NOW",
  "link_url": "https://my-store.myshopify.com/products/earbuds"
}
```

Response: `AdSchema`

### POST /ads/facebook/images
Upload image to the connected ad account.

Request: `multipart/form-data` with field `image`.

Response: raw Facebook `adimages` response.

### PATCH /ads/facebook/ads/{ad_id}
Update ad (status, creative).

### GET /ads/facebook/metrics
Fetch performance metrics with aggregation.

Query params:
| Name | Type | Required | Default |
|---|---|---|---|
| level | string | yes | — |
| ids | string | no | all active |
| date_from | string | no | last 7 days |
| date_to | string | no | today |
| breakdown | string | no | none |

Level: `campaign`, `adset`, `ad`.
Breakdown: `day`, `age`, `gender`, `country`, `placement`, `none`.

Response: `{ "items": [{ "id": "...", "name": "...", "metrics": MetricsSchema, "date": "..." }] }`

Example (ad set metrics for last 7 days):
```bash
curl -s "{{api_base_url}}/ads/facebook/metrics?level=adset&date_from=2025-03-11&date_to=2025-03-18" \
  -H "Authorization: Bearer $AGENT_API_KEY"
```

### GET /ads/facebook/audiences
List custom and lookalike audiences.

Response: `{ "items": AudienceSchema[] }`

### POST /ads/facebook/audiences/lookalike
Create a lookalike audience.

Body:
| Field | Type | Required |
|---|---|---|
| name | string | yes |
| source_audience_id | string | yes |
| country | string | yes |
| ratio | float | yes (0.01 to 0.20) |

Response: `AudienceSchema`

### GET /ads/facebook/targeting/interests?q=...
Search interest clusters for targeting.

Response: `{ "items": [{ "id": "...", "name": "...", ... }] }`

### GET /ads/facebook/targeting/locations?q=...
Search geo locations for targeting.

Response: `{ "items": [{ "key": "...", "name": "...", ... }] }`

### GET /ads/facebook/adcreatives
List ad creatives for analysis and creative refresh planning.

Response: `{ "items": [{ "id": "...", "name": "...", "title": "...", "body": "..." }] }`

## Campaign creation workflow (recommended)

1. Create campaign (PAUSED): `POST /ads/facebook/campaigns`
2. Create ad set(s): `POST /ads/facebook/adsets`
3. Create ad(s): `POST /ads/facebook/ads`
4. Return plan to supervisor for approval.
5. On approval — activate: `PATCH /ads/facebook/campaigns/{id}` with `{"status": "ACTIVE"}`

## Optimization workflow (recommended)

1. Fetch metrics: `GET /ads/facebook/metrics?level=adset&date_from=...`
2. Evaluate against rules (CPA, ROAS, frequency thresholds).
3. Return action list to supervisor.
4. On approval — execute pauses, budget changes, duplications.

## Guardrails
- Never create campaigns as ACTIVE — always PAUSED first, activate after approval.
- Never increase daily budget by more than 20% in one change.
- Retry failed API calls at most twice.
- Do not print API tokens in responses.
- Always include date range when reporting metrics.
- Use `attribution_window: "7d_click"` as default for conversion metrics.
