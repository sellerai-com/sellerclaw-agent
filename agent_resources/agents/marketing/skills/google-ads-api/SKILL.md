---
name: google-ads-api
description: "Operate Google Ads (and Performance Max) for the connected account through the `sellerclaw` CLI: list / create / pause / update campaigns and ad groups, pull performance metrics, manage PMax asset groups, sync Merchant Center products, and pull keyword ideas. Use whenever the task targets Google Ads — Search, Shopping, or Performance Max — for the owner's connected integration; the customer/account is resolved server-side, no GAQL needed. For campaign workflow templates (creation, optimization, scaling, A/B) use `campaign-playbook`; for catalog inputs use `catalog`."
---

# Google Ads (via `sellerclaw` CLI)

All commands are subcommands of `sellerclaw google-ads …`. Output is JSON on stdout; structured errors on stderr with non-zero exit codes (1=user/api, 2=server/network, 3=auth). Customer/Merchant credentials resolve server-side from the connected integration.

**Conventions:** money is float in account currency; dates are `YYYY-MM-DD`; bodies are JSON via `-b '<inline>'`, `-b @file.json`, or `-b @-` (stdin).

Pick the section by **task intent**.

---

## Browse / inspect

| Intent | Command |
|---|---|
| List campaigns | `sellerclaw google-ads get-campaigns [--status ENABLED\|PAUSED\|REMOVED] [--type SHOPPING\|PERFORMANCE_MAX\|SEARCH] [--limit 50]` |
| One campaign | `sellerclaw google-ads get-campaign <campaign_id>` |
| Ad groups in campaign | `sellerclaw google-ads get-campaign-groups <campaign_id>` |
| Asset groups (PMax) | `sellerclaw google-ads get-asset-groups <campaign_id>` |
| Merchant Center inventory | `sellerclaw google-ads get-products` |
| Optimization recommendations | `sellerclaw google-ads get-recommendations` |
| Action log (audit trail) | `sellerclaw google-ads get-action-log [--entity-id <id>] [--days 1..90]` |

---

## Metrics

**Command:** `sellerclaw google-ads get-metrics [--level {campaign\|ad_group\|product_group}] [--ids c1,c2] [--date-from YYYY-MM-DD] [--date-to YYYY-MM-DD] [--breakdown {none\|day\|device}]`

Defaults: last 7 days, `level=campaign`, `breakdown=none`. Always include the resolved date range when reporting numbers.

Per-row metrics: `spend`, `impressions`, `clicks`, `ctr`, `avg_cpc`, `conversions`, `conversion_value`, `cpa`, `roas` — full list in `references/data-model.md`.

---

## Create a campaign (always PAUSED first)

**Command:** `sellerclaw google-ads create-campaign -b '<json>'`

Supported `type` values: `SHOPPING`, `PERFORMANCE_MAX`. Server-forced `PAUSED` on create.

**Required body fields:** `name`, `type`, `daily_budget`, `bidding_strategy`. **Optional:** `target_roas`, `merchant_id` (Shopping only — usually inferred), `campaign_priority` (Shopping, 0–2), `asset_group` (PMax only).

Shopping example:

```bash
sellerclaw google-ads create-campaign -b '{
  "name": "Shopping - Spring Sale",
  "type": "SHOPPING",
  "daily_budget": 30.0,
  "bidding_strategy": "MAXIMIZE_CONVERSIONS"
}'
```

Performance Max example:

```bash
sellerclaw google-ads create-campaign -b '{
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

Activate only after supervisor approval: `sellerclaw google-ads patch-campaign <id> -b '{"status":"ENABLED"}'`.

---

## Update / pause campaign or ad group

| Intent | Command |
|---|---|
| Patch campaign | `sellerclaw google-ads patch-campaign <campaign_id> -b '<json-delta>'` |
| Patch ad group | `sellerclaw google-ads patch-group <adgroup_id> -b '<json-delta>'` |
| Patch PMax asset group | `sellerclaw google-ads patch-asset-group <asset_group_id> -b '<json-delta>'` |

Patchable campaign fields: `name`, `status`, `daily_budget`, `bidding_strategy`, `target_roas`. Budget delta hard cap: ±20%.
Patchable ad-group fields: `name`, `status`, `cpc_bid`.

---

## Create ad group

**Command:** `sellerclaw google-ads create-group -b '<json>'`

**Required:** `campaign_id`, `name`. **Optional:** `cpc_bid`. Server forces `PAUSED` on create.

---

## Keyword ideas

**Command:** `sellerclaw google-ads post-keyword-ideas -b '<json>'`

Body: `keywords` (array of seed strings, required), `language` (optional), `geo_target_constants` (optional list of geo constant resource names). Use to seed Search campaigns or audit relevance.

---

## Recommended flows

**Shopping campaign:** verify Merchant inventory (`get-products`) → `create-campaign` (`type=SHOPPING`, PAUSED) → tune ad groups (`create-group` / `patch-group`) → validate early metrics (`get-metrics --breakdown day`) → present to supervisor → activate via `patch-campaign`.

**Performance Max:** `create-campaign` with `asset_group` payload (PAUSED) → manage assets via `get-asset-groups` / `patch-asset-group` → review `get-recommendations` → wait ~14 days for learning before major budget/strategy changes → activate via `patch-campaign` after approval.

---

## Guardrails

- Campaign and ad-group creation is server-forced to `PAUSED` — activate only after approval.
- Budget patches are server-capped at ±20% per call.
- PMax requires a learning period before meaningful optimization.
- Mutation endpoints are rate-limited; do not burst-update.
- Retry a failed CLI call at most twice; then return a blocker.
- Never echo or log auth tokens.
- Always include the date range when reporting metrics.

---

## Reference

- **Full data models** (campaigns, ad groups, asset groups, metrics, Merchant products — every field): `references/data-model.md`.
- **OpenAPI source of truth:** `sellerclaw describe <operation_id>`; discover ops via `sellerclaw list-operations --tag google-ads`. Use when this skill or `--help` is not enough.
