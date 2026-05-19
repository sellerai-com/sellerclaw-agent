---
name: google-ads-api
description: "Operate Google Ads (Shopping, Performance Max) on the connected account via the `sellerclaw` CLI: list/create/pause/update campaigns and ad groups, pull metrics, manage PMax asset groups, sync Merchant Center products, fetch keyword ideas. Use when the task says 'pause this Google campaign', 'launch a Shopping campaign', 'recreate this Google campaign', 'pull Google Ads metrics', 'create a PMax asset group', 'sync products to Merchant Center', 'find keyword ideas', 'change the bid/budget', or any task touching Google Ads state or metrics. Customer/Merchant credentials resolve server-side — never pass them. For workflow templates (intake, asset prep, decision tree, optimization, scaling, emergency rules) use `campaign-playbook`; for product / store context use `catalog` and `sales-channels`."
---

# Google Ads (via `sellerclaw` CLI)

All commands are subcommands of `sellerclaw google-ads …`. Output is JSON on stdout; structured errors on stderr with exit codes (1=user/api, 2=server/network, 3=auth). Customer/Merchant credentials resolve server-side from the connected integration.

**Conventions:** money is float in account currency (min `0.01`); dates are `YYYY-MM-DD`; bodies are JSON via `-b '<inline>'`, `-b @file.json`, or `-b @-` (stdin).

**Supported campaign types via this CLI:** `SHOPPING`, `PERFORMANCE_MAX`. Pure `SEARCH` campaigns cannot be created here — return a blocker if the supervisor asks for one.

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

`REMOVED` (= deleted) campaigns are read-only. Treat any "recreate this campaign" task as a **new launch** — `campaign-playbook` section 0 — not as a literal copy of the removed entity.

---

## Metrics

`sellerclaw google-ads get-metrics [--level {campaign\|ad_group\|product_group}] [--ids c1,c2] [--date-from YYYY-MM-DD] [--date-to YYYY-MM-DD] [--breakdown {none\|day\|device}]`

Defaults: last 7 days, `level=campaign`, `breakdown=none`. Always include the resolved date range when reporting numbers.

Metric fields per row: `spend`, `impressions`, `clicks`, `ctr`, `avg_cpc`, `conversions`, `conversion_value`, `cpa`, `roas`. Full schema in `references/data-model.md`.

---

## Create a campaign (always PAUSED first — server-enforced)

`sellerclaw google-ads create-campaign -b '<json>'`

**Required body:** `name`, `type` (`SHOPPING` or `PERFORMANCE_MAX`), `daily_budget` (> 0; see budget floors in `campaign-playbook`), `bidding_strategy`.

**Bidding strategy choice (most cases):**

| Goal | Strategy | Notes |
|---|---|---|
| Sales, no ROAS history | `MAXIMIZE_CONVERSIONS` | Default for new accounts |
| Sales, ROAS history available | `MAXIMIZE_CONVERSION_VALUE` + `target_roas` | Better unit economics |
| Manual control (rare) | `MANUAL_CPC` | Only for Shopping with bid expertise |

### Shopping

```bash
sellerclaw google-ads create-campaign -b '{
  "name": "Shopping - Spring Sale",
  "type": "SHOPPING",
  "daily_budget": 30.0,
  "bidding_strategy": "MAXIMIZE_CONVERSIONS",
  "campaign_priority": 1
}'
```

Pre-flight: `get-products` must return at least one `availability: "in stock"` row, otherwise the campaign has nothing to show. Block if empty.

### Performance Max — strict asset minimums

**Before the call, ensure the `asset_group` has at least:**

| Field | Min | Max | Per-item limit |
|---|---|---|---|
| `headlines` | **5** | 15 | ≤30 chars each, unique, no all-caps |
| `descriptions` | **3** | 5 | ≤90 chars each (one ≤60 recommended) |
| `image_urls` | **2** | 20 | ≥1 landscape (1.91:1, e.g. 1200×628) and ≥1 square (1:1, e.g. 1200×1200), HTTPS |
| `logo_urls` | **1** | 5 | ≥1 square (1:1, 1200×1200), HTTPS |
| `final_url` | 1 | 1 | Valid HTTPS URL on the advertised domain |
| `name` | 1 | 1 | ≤80 chars |

Google additionally enforces a `business_name` (≤25 chars) — currently passed via the asset group `name` slot on this CLI; keep `name` ≤25 if business name is being inferred from it. Confirm by reading the warning field in the create response.

If any minimum is unmet, **do not call create** — return a blocker listing the specific missing field and count. The proxy may accept under-spec input and create a broken campaign; that's worse than failing fast.

```bash
sellerclaw google-ads create-campaign -b '{
  "name": "PMax - Summer Promo",
  "type": "PERFORMANCE_MAX",
  "daily_budget": 50.0,
  "bidding_strategy": "MAXIMIZE_CONVERSIONS",
  "asset_group": {
    "name": "Main",
    "final_url": "https://store.example.com/p/widget",
    "headlines": ["Summer Widget Sale", "Free Shipping Today", "Top-Rated Widget", "Save 30% This Week", "Order Before Friday"],
    "descriptions": ["Free 2-day shipping on all orders.", "Trusted by 10,000+ customers worldwide.", "30-day money-back guarantee."],
    "image_urls": ["https://cdn.example.com/widget-landscape.jpg", "https://cdn.example.com/widget-square.jpg"],
    "logo_urls": ["https://cdn.example.com/logo-square.png"]
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

`sellerclaw google-ads create-group -b '<json>'`

Required: `campaign_id`, `name`. Optional: `cpc_bid`. Server forces `PAUSED` on create.

---

## Keyword ideas

`sellerclaw google-ads post-keyword-ideas -b '<json>'`

Body: `keywords` (seed terms, required, non-empty), `language` (optional), `geo_target_constants` (optional list of resource names). Use to vet whether a product has enough Search demand before recommending a budget level, or to seed Shopping campaign naming.

---

## Recommended flows

**Shopping campaign:** verify Merchant inventory (`get-products`) → `create-campaign` (`type=SHOPPING`, PAUSED) → optionally tune ad groups (`create-group` / `patch-group`) → check early metrics (`get-metrics --breakdown day`) → present to supervisor → activate via `patch-campaign`.

**Performance Max:** prepare assets per minimums above → `create-campaign` with `asset_group` payload (PAUSED) → review `get-recommendations` after 24h → wait ≥14 days for learning before major budget/strategy changes → activate via `patch-campaign` after approval.

**Recreate a removed campaign:** treat as new launch. Pull product/store context, rebuild assets, decide type via `campaign-playbook` decision tree, validate budget viability — do not copy fields from the `REMOVED` campaign blindly.

---

## Guardrails

- Campaign and ad-group creation forced to `PAUSED` server-side; activate only after explicit approval.
- Budget patches capped at ±20% per call (server-enforced).
- PMax minimum-asset rules above are **agent-enforced** — proxy will not catch missing fields.
- PMax requires a ~14-day learning period; resist optimization changes before then.
- Daily budget must be ≥ `$0.01`; PMax practical floor is `3 × target_cpa` (default `$45`).
- Mutation endpoints are rate-limited; do not burst-update.
- Retry a failed CLI call at most twice; then return a blocker.
- Never echo or log auth tokens.
- Always include the resolved date range when reporting metrics.

---

## Reference

- **Full data models** — `references/data-model.md`.
- **OpenAPI source of truth** — `sellerclaw describe <operation_id>`; discover ops via `sellerclaw list-operations --tag google-ads`.
