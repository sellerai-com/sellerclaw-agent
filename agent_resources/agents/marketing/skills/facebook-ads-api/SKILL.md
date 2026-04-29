---
name: facebook-ads-api
description: "Operate Facebook / Meta Ads for the connected ad account through the `sellerclaw` CLI: list / create / pause / update campaigns, ad sets, and ads; manage custom and lookalike audiences; pull performance metrics by entity and date range. Use whenever the task targets Facebook or Instagram advertising — the ad account ID is resolved server-side, no manual id needed. For campaign workflow templates (creation, optimization, scaling, A/B, emergency rules) use `campaign-playbook`; for product catalog inputs use `catalog`."
---

# Facebook Ads (via `sellerclaw` CLI)

All commands are subcommands of `sellerclaw facebook-ads …`. Output is JSON on stdout; structured errors go to stderr with non-zero exit codes (1=user/api, 2=server/network, 3=auth). Ad account is resolved server-side — never pass it.

**Conventions:** money is float in account currency; dates are `YYYY-MM-DD`; bodies are JSON via `-b '<inline>'`, `-b @file.json`, or `-b @-` (stdin).

Pick the section by **task intent**.

---

## Browse / inspect

| Intent | Command |
|---|---|
| List campaigns | `sellerclaw facebook-ads get-campaigns [--status ACTIVE\|PAUSED\|ARCHIVED] [--limit 50]` |
| One campaign | `sellerclaw facebook-ads get-campaign <campaign_id>` |
| Ad sets in campaign | `sellerclaw facebook-ads get-campaign-adsets <campaign_id>` |
| Existing creatives | `sellerclaw facebook-ads list-creatives` |
| Audiences (custom + lookalike) | `sellerclaw facebook-ads list-audiences` |
| Action log (audit trail) | `sellerclaw facebook-ads get-action-log [--entity-id <id>] [--days 1..90]` |

---

## Metrics

**Command:** `sellerclaw facebook-ads get-metrics --level {campaign\|adset\|ad} [--ids c1,c2] [--date-from YYYY-MM-DD] [--date-to YYYY-MM-DD] [--breakdown {none\|day\|age\|gender\|country\|placement}]`

Defaults: last 7 days, `breakdown=none`, all active entities. Always include the resolved date range when reporting numbers.

Per-row metrics: `spend`, `impressions`, `clicks`, `ctr`, `cpc`, `conversions`, `cpa`, `roas`, `cpm`, `frequency`, `reach` — full list in `references/data-model.md`.

---

## Create a campaign (always PAUSED first)

**Command:** `sellerclaw facebook-ads create-campaign -b '<json>'`

**Required body fields:** `name`, `objective`. **Common optional:** `daily_budget` or `lifetime_budget`, `status` (server defaults to `PAUSED`).

```bash
sellerclaw facebook-ads create-campaign -b '{
  "name": "Summer Sale - Wireless Earbuds",
  "objective": "CONVERSIONS",
  "daily_budget": 50.0,
  "status": "PAUSED"
}'
```

Activate only after supervisor approval: `sellerclaw facebook-ads patch-campaign <id> -b '{"status":"ACTIVE"}'`.

---

## Update / pause campaign or ad set

| Intent | Command |
|---|---|
| Patch campaign (status/budget/name) | `sellerclaw facebook-ads patch-campaign <campaign_id> -b '<json-delta>'` |
| Patch ad set | `sellerclaw facebook-ads patch-adset <adset_id> -b '<json-delta>'` |
| Patch ad | `sellerclaw facebook-ads patch <ad_id> -b '<json-delta>'` |

Send only the fields you want to change. Budget delta hard cap: ±20% per call.

---

## Create ad set

**Command:** `sellerclaw facebook-ads create-adset -b '<json>'`

**Required:** `campaign_id`, `name`, `daily_budget`, `bid_strategy`, `optimization_goal`, `targeting`. **Optional:** `bid_amount` (required for `cost_cap`/`bid_cap`), `start_time`, `end_time`, `status`.

Targeting object minimum: `age_min`, `age_max`, `countries`, optional `interests[]`, `custom_audiences[]`, `lookalike_audiences[]`, `genders`, `placements` (default `automatic`). Full schema in `references/data-model.md`.

---

## Duplicate ad set (A/B, scaling)

**Command:** `sellerclaw facebook-ads duplicate-adset <adset_id> -b '<json>'`

All body fields optional: `name`, `campaign_id`, `daily_budget`. Omit to inherit from source.

---

## Create ad

**Command:** `sellerclaw facebook-ads create -b '<json>'`

**Required:** `ad_set_id`, `name`, `creative`. **Optional:** `status` (default `PAUSED`).

Creative object: `title`, `body`, `image_url` *or* `video_url`, `call_to_action` (`SHOP_NOW`, `LEARN_MORE`, …), `link_url`.

```bash
sellerclaw facebook-ads create -b '{
  "ad_set_id": "120210...",
  "name": "Earbuds — image A",
  "creative": {
    "title": "50% Off Wireless Earbuds",
    "body": "Premium sound. All-day comfort. Free shipping.",
    "image_url": "https://...",
    "call_to_action": "SHOP_NOW",
    "link_url": "https://store.example.com/products/earbuds"
  }
}'
```

---

## Upload creative image

**Command:** `sellerclaw facebook-ads upload-image` — multipart upload of an image asset to the ad account. Returns the raw Facebook `adimages` payload (image hash / URL go into the next `create` body).

---

## Audiences

| Intent | Command |
|---|---|
| List audiences | `sellerclaw facebook-ads list-audiences` |
| Lookalike from source | `sellerclaw facebook-ads create-lookalike-audience -b '<json>'` |

Lookalike body: `name`, `source_audience_id`, `country` (2-letter ISO), `ratio` (0.01–0.20).

Never build audiences from raw customer data — only use platform-side sources.

---

## Targeting search (interests, locations)

| Intent | Command |
|---|---|
| Find interest IDs | `sellerclaw facebook-ads search-interests --q "fitness"` |
| Find geo IDs | `sellerclaw facebook-ads search-locations --q "berlin"` |

Use returned `id` (interests) or `key` (locations) inside the `targeting` object of `create-adset`.

---

## Recommended flows

**Campaign creation:** create campaign (PAUSED) → create ad set(s) → create ad(s) → present plan to supervisor → on approval, `patch-campaign` to `ACTIVE`.

**Optimization pass:** `get-metrics --level adset` → evaluate vs strategy thresholds → return action list → on approval, execute pauses, budget patches, duplications.

---

## Guardrails

- Never create campaigns as `ACTIVE` — always `PAUSED` first, activate after approval.
- Never increase a daily budget by more than 20% in one change.
- Retry a failed CLI call at most twice; then return a blocker.
- Never echo or log auth tokens.
- Always include date range and `attribution_window: "7d_click"` when reporting conversion metrics.

---

## Reference

- **Full data models** (campaigns, ad sets, ads, creatives, metrics, audiences — every field): `references/data-model.md`.
- **OpenAPI source of truth:** `sellerclaw describe <operation_id>`; discover ops via `sellerclaw list-operations --tag facebook-ads`. Use when this skill or `--help` is not enough.
