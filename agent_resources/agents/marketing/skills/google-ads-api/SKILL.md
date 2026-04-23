---
name: google-ads-api
description: "Work with Google Ads through sellerclaw-api endpoints for campaigns, ad groups, metrics, Merchant products, asset groups, and keyword ideas."
---

# Google Ads API Skill

## Goal
Reference guide for `sellerclaw-api` endpoints used to manage Google Ads campaigns.

## Base URL and Authentication
- Base URL: `{{api_base_url}}`
- Auth header: `Authorization: Bearer $AGENT_API_KEY`
- Do not print token values in logs or messages.

## Conventions
- Use `exec curl` for HTTP requests.
- All request/response bodies are JSON.
- Google customer/account credentials are resolved server-side from connected integration.
- Proxy hides GAQL complexity; use simple REST endpoints.
- Monetary values are returned as floats in account currency.
- Date format: `YYYY-MM-DD`.

## Schemas (high-level)

`GoogleCampaignSchema`
| Field | Type | Notes |
|---|---|---|
| id | string | Google campaign ID |
| name | string | |
| status | string | ENABLED, PAUSED, REMOVED |
| type | string | SHOPPING, PERFORMANCE_MAX, SEARCH, etc. |
| bidding_strategy | string | MAXIMIZE_CONVERSIONS, TARGET_ROAS, etc. |
| target_roas | float\|null | Target ROAS value |
| daily_budget | float | Daily budget |
| budget_resource_name | string | Internal Google budget resource |
| warning | string\|null | Optional warning (e.g. PMax learning period) |

`GoogleAdGroupSchema`
| Field | Type | Notes |
|---|---|---|
| id | string | |
| campaign_id | string | |
| name | string | |
| status | string | ENABLED, PAUSED |
| cpc_bid | float\|null | Max CPC bid |

`GoogleMetricsSchema`
| Field | Type | Notes |
|---|---|---|
| spend | float | Cost in account currency |
| impressions | int | |
| clicks | int | |
| ctr | float | % |
| avg_cpc | float | Average CPC |
| conversions | float | Can be fractional |
| conversion_value | float | Revenue attributed |
| cpa | float | Cost per conversion |
| roas | float | conversion_value / spend |

`GoogleAssetGroupSchema`
| Field | Type | Notes |
|---|---|---|
| id | string | |
| campaign_id | string | |
| name | string | |
| status | string | ENABLED, PAUSED |
| resource_name | string | Google resource path |

`MerchantProductSchema`
| Field | Type | Notes |
|---|---|---|
| product_id | string | Merchant product id |
| offer_id | string | Merchant offer id |
| title | string | |
| channel | string | online/local |
| availability | string | in stock / out of stock |
| status | string | Destination statuses summary |
| issues | list | Item-level issues/disapprovals |

## Endpoints

### GET /ads/google/campaigns
List campaigns with filters.

Query params:
| Name | Type | Required | Default |
|---|---|---|---|
| status | string | no | all |
| type | string | no | all |
| limit | int | no | 50 |

Response: `{ "items": GoogleCampaignSchema[] }`

Example:
```bash
curl -s "{{api_base_url}}/ads/google/campaigns?status=PAUSED&type=SHOPPING&limit=20" \
  -H "Authorization: Bearer $AGENT_API_KEY"
```

### GET /ads/google/campaigns/{campaign_id}
Get campaign details.

Response: `GoogleCampaignSchema`

### POST /ads/google/campaigns
Create a campaign.

Supported types:
- `SHOPPING`
- `PERFORMANCE_MAX`

Guardrails applied by server:
- Campaign is always created as `PAUSED`.
- Shopping requires `merchant_id` (from connected credentials or request body).
- PMax response may include learning warning.

Body (common):
| Field | Type | Required |
|---|---|---|
| name | string | yes |
| type | string | yes |
| daily_budget | float | yes |
| bidding_strategy | string | yes |
| target_roas | float | no |
| merchant_id | string | no (SHOPPING only, optional if already connected) |
| campaign_priority | int | no (SHOPPING only) |
| asset_group | object | no (PERFORMANCE_MAX) |

Shopping example:
```bash
curl -s -X POST "{{api_base_url}}/ads/google/campaigns" \
  -H "Authorization: Bearer $AGENT_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Shopping - Spring Sale",
    "type": "SHOPPING",
    "daily_budget": 30.0,
    "bidding_strategy": "MAXIMIZE_CONVERSIONS"
  }'
```

Performance Max example:
```bash
curl -s -X POST "{{api_base_url}}/ads/google/campaigns" \
  -H "Authorization: Bearer $AGENT_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "PMax - Summer Promo",
    "type": "PERFORMANCE_MAX",
    "daily_budget": 50.0,
    "bidding_strategy": "MAXIMIZE_CONVERSIONS",
    "asset_group": {
      "name": "Main Asset Group",
      "final_url": "https://store.example.com",
      "headlines": ["Summer Sale", "Free Shipping", "Best Deals"],
      "descriptions": ["Shop now", "Top picks this week"],
      "image_urls": ["https://cdn.example.com/banner.jpg"],
      "logo_urls": ["https://cdn.example.com/logo.png"]
    }
  }'
```

### PATCH /ads/google/campaigns/{campaign_id}
Update campaign fields.

Supported fields:
- `name`
- `status`
- `daily_budget`

Guardrail:
- `daily_budget` change must be within 20%.

### GET /ads/google/campaigns/{campaign_id}/adgroups
List ad groups for a campaign.

Response: `{ "items": GoogleAdGroupSchema[] }`

### POST /ads/google/adgroups
Create ad group (created as `PAUSED`).

Body:
| Field | Type | Required |
|---|---|---|
| campaign_id | string | yes |
| name | string | yes |
| cpc_bid | float | no |
| status | string | no (ignored, server forces PAUSED) |

### PATCH /ads/google/adgroups/{adgroup_id}
Update ad group fields (`name`, `status`, `cpc_bid`).

### GET /ads/google/metrics
Fetch metrics.

Query params:
| Name | Type | Required | Default |
|---|---|---|---|
| level | string | no | campaign |
| ids | string | no | all |
| date_from | string | no | last 7 days |
| date_to | string | no | today |
| breakdown | string | no | none |

Level: `campaign`, `ad_group`, `product_group`
Breakdown: `none`, `day`, `device`

Response: `{ "items": [{ "id": "...", "name": "...", "date": "...", "metrics": GoogleMetricsSchema }] }`

Example:
```bash
curl -s "{{api_base_url}}/ads/google/metrics?level=campaign&date_from=2026-03-01&date_to=2026-03-24&breakdown=day" \
  -H "Authorization: Bearer $AGENT_API_KEY"
```

### GET /ads/google/products
List Merchant Center products.

Response: `{ "items": MerchantProductSchema[] }`

### GET /ads/google/campaigns/{campaign_id}/asset-groups
List campaign asset groups.

Response: `{ "items": GoogleAssetGroupSchema[] }`

### PATCH /ads/google/asset-groups/{asset_group_id}
Update asset group fields (`name`, `status`).

### POST /ads/google/keywords/ideas
Generate keyword ideas.

Body:
| Field | Type | Required |
|---|---|---|
| keywords | list[string] | yes |
| language | string | no |
| geo_target_constants | list[string] | no |

### GET /ads/google/recommendations
Fetch optimization recommendations.

Response: Google recommendations payload.

## Workflow: Shopping Campaign (recommended)
1. Verify Merchant inventory: `GET /ads/google/products`.
2. Create campaign as PAUSED: `POST /ads/google/campaigns` with `type=SHOPPING`.
3. Create or tune ad groups: `POST /ads/google/adgroups` / `PATCH /ads/google/adgroups/{id}`.
4. Validate early metrics (day breakdown): `GET /ads/google/metrics?...&breakdown=day`.
5. Present plan/results to supervisor; activate with `PATCH /ads/google/campaigns/{id}` only after approval.

## Workflow: Performance Max (recommended)
1. Create PMax campaign with asset group payload: `POST /ads/google/campaigns`.
2. Keep PAUSED until approval.
3. Manage asset groups via `GET/PATCH /ads/google/.../asset-groups`.
4. Review recommendations via `GET /ads/google/recommendations`.
5. Wait for learning period (~14 days) before major budget or strategy changes.

## Guardrails
- Campaign and ad group creation is server-forced to `PAUSED`.
- Budget PATCH is limited to <=20% change.
- PMax requires a learning period before meaningful optimization.
- Mutation routes are rate-limited to reduce accidental burst updates.
- Never expose OAuth/API tokens in agent outputs.
