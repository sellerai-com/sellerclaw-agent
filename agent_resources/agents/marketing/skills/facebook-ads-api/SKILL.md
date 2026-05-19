---
name: facebook-ads-api
description: "Operate Facebook / Instagram / Meta Ads on the connected ad account via the `sellerclaw` CLI: list/create/pause/update campaigns, ad sets, ads; manage custom and lookalike audiences; pull metrics by entity and date range. Use when the task says 'pause this Meta campaign', 'launch a new Facebook ad', 'recreate this campaign', 'pull Meta ad performance', 'create a lookalike audience', 'check Instagram ads spend', 'increase the daily budget on ad set X', 'duplicate this campaign', or any task touching Facebook/Instagram/Meta ad-account state or metrics. Ad account ID is resolved server-side — no manual id needed. For workflow templates (intake, asset prep, decision tree, optimization, scaling, A/B, emergency rules) use `campaign-playbook`; for product / store context use `catalog` and `sales-channels`."
---

# Facebook Ads (via `sellerclaw` CLI)

All commands are subcommands of `sellerclaw facebook-ads …`. Output is JSON on stdout; structured errors go to stderr with exit codes (1=user/api, 2=server/network, 3=auth). Ad account is resolved server-side — never pass it.

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

`ARCHIVED` campaigns are read-only. Treat any "recreate this campaign" task as a **new launch** — `campaign-playbook` section 0 — not as a literal copy.

---

## Metrics

`sellerclaw facebook-ads get-metrics --level {campaign|adset|ad} [--ids c1,c2] [--date-from YYYY-MM-DD] [--date-to YYYY-MM-DD] [--breakdown {none|day|age|gender|country|placement}]`

Defaults: last 7 days, `breakdown=none`, all active entities. Always include the resolved date range and `attribution_window: "7d_click"` when reporting conversion metrics.

Per-row metrics: `spend`, `impressions`, `clicks`, `ctr`, `cpc`, `conversions`, `cpa`, `roas`, `cpm`, `frequency`, `reach` — full list in `references/data-model.md`.

---

## Create a campaign (always PAUSED first)

`sellerclaw facebook-ads create-campaign -b '<json>'`

**Required body:** `name`, `objective`. **Common optional:** `daily_budget` or `lifetime_budget` (use CBO at campaign level when running ≥3 ad sets), `status` (server defaults to `PAUSED`).

**Objective choice:**

| Goal | Objective | Pixel needed? |
|---|---|---|
| Purchases / signups | `CONVERSIONS` | **Yes** — pixel with event matching the conversion. Without it, learning never exits. |
| Catalog DPA | `CATALOG_SALES` | Yes — product feed + pixel events. |
| Clicks / landing page views | `TRAFFIC` | No — fine for early validation without pixel. |
| Awareness | `REACH` | No |

If the supervisor asks for `CONVERSIONS` but no pixel events have been registered, downgrade to `TRAFFIC` in the launch plan and flag the missing pixel as a blocker.

```bash
sellerclaw facebook-ads create-campaign -b '{
  "name": "Summer Sale - Wireless Earbuds",
  "objective": "CONVERSIONS",
  "daily_budget": 50.0,
  "status": "PAUSED"
}'
```

Activate only after explicit supervisor approval: `sellerclaw facebook-ads patch-campaign <id> -b '{"status":"ACTIVE"}'`.

---

## Update / pause campaign or ad set

| Intent | Command |
|---|---|
| Patch campaign | `sellerclaw facebook-ads patch-campaign <campaign_id> -b '<json-delta>'` |
| Patch ad set | `sellerclaw facebook-ads patch-adset <adset_id> -b '<json-delta>'` |
| Patch ad | `sellerclaw facebook-ads patch <ad_id> -b '<json-delta>'` |

Send only the fields you want to change. Budget delta hard cap: ±20% per call.

---

## Create ad set

`sellerclaw facebook-ads create-adset -b '<json>'`

Required: `campaign_id`, `name`, `daily_budget`, `bid_strategy`, `optimization_goal`, `targeting`. Optional: `bid_amount` (required for `cost_cap`/`bid_cap`), `start_time`, `end_time`, `status`.

**Bid strategy choice:** `lowest_cost` for new ad sets (no history); `cost_cap` with `bid_amount` once you have a stable CPA you want to defend; avoid `bid_cap` unless explicitly requested.

**Optimization goal choice (objective-dependent):** `OFFSITE_CONVERSIONS` for `CONVERSIONS`, `LINK_CLICKS` for `TRAFFIC`, `LANDING_PAGE_VIEWS` to filter bounce-quality. Mismatching goal to objective is the #1 cause of stuck learning.

Targeting object minimum: `age_min`, `age_max`, `countries`, optional `interests[]`, `custom_audiences[]`, `lookalike_audiences[]`, `genders`, `placements` (default `automatic`). Full schema in `references/data-model.md`.

---

## Create ad — creative requirements

`sellerclaw facebook-ads create -b '<json>'`

Required: `ad_set_id`, `name`, `creative`. Optional: `status` (default `PAUSED`).

Creative payload — every ad needs all of:

| Field | Required | Limits |
|---|---|---|
| `title` (headline) | yes | ≤40 chars recommended (truncates on mobile) |
| `body` (primary text) | yes | ≤125 chars before "See more" |
| `image_url` **or** `video_url` | yes (one) | HTTPS; image min 1080×1080 (1:1) or 1080×1350 (4:5); video MP4/MOV ≤4 GB, ≤15 min |
| `call_to_action` | yes | Enum: `SHOP_NOW`, `LEARN_MORE`, `SIGN_UP`, `SUBSCRIBE`, `DOWNLOAD`, … |
| `link_url` | yes | HTTPS destination |

If the source product has only smaller images, mint a hosted URL via `sellerclaw agent-files from-url --url <product_image_url>` and pass the returned `download_url`. Do not call `create` with a missing field — failure occurs at Meta's side and burns rate-limit budget.

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

## Duplicate ad set (A/B, scaling)

`sellerclaw facebook-ads duplicate-adset <adset_id> -b '<json>'`

All body fields optional: `name`, `campaign_id`, `daily_budget`. Omit to inherit from source. Always change exactly one dimension (creative, audience, copy, or budget) per A/B fork — see `campaign-playbook` section 6.

---

## Upload creative image

`sellerclaw facebook-ads upload-image` — multipart upload to the ad account. Returns the raw Facebook `adimages` payload (image hash / URL go into the next `create` body). Use when the source image needs to live in Facebook's own CDN rather than your storefront.

---

## Audiences

| Intent | Command |
|---|---|
| List audiences | `sellerclaw facebook-ads list-audiences` |
| Lookalike from source | `sellerclaw facebook-ads create-lookalike-audience -b '<json>'` |

Lookalike body: `name`, `source_audience_id`, `country` (2-letter ISO), `ratio` (0.01–0.20; tighter = more similar). Use the 1–2% ratio for prospecting from a strong source (purchasers / high-LTV pixel events), 5–10% to broaden reach once 1–2% saturates.

Never build audiences from raw customer data — only via platform-side sources.

---

## Targeting search (interests, locations)

| Intent | Command |
|---|---|
| Find interest IDs | `sellerclaw facebook-ads search-interests --q "fitness"` |
| Find geo IDs | `sellerclaw facebook-ads search-locations --q "berlin"` |

Use returned `id` (interests) or `key` (locations) inside the `targeting` object of `create-adset`.

---

## Recommended flows

**Campaign creation:** intake (`campaign-playbook` §0) → create campaign (PAUSED) → create 2–5 ad sets covering distinct audiences → create 2–3 ads per ad set (creative variants) → present plan with budgets + reach estimates → on approval, `patch-campaign` to `ACTIVE`.

**Optimization pass:** `get-metrics --level adset` over `learning_period_days` → evaluate vs strategy thresholds → return action list (kill / scale / refresh) → on approval, execute pauses, budget patches, duplications.

**Recreate a removed campaign:** treat as new launch. Pull product/store context, rebuild creative, decide objective via `campaign-playbook` decision tree, validate budget viability.

---

## Guardrails

- Never create campaigns as `ACTIVE` — always `PAUSED`, activate after approval.
- Never increase a daily budget by more than 20% in one change.
- Never call `create` with an incomplete creative — validate locally first.
- Don't use `CONVERSIONS` objective without an active pixel; downgrade to `TRAFFIC` and flag.
- Retry a failed CLI call at most twice; then return a blocker.
- Never echo or log auth tokens.
- Always include date range + `attribution_window: "7d_click"` when reporting conversion metrics.

---

## Reference

- **Full data models** — `references/data-model.md`.
- **OpenAPI source of truth** — `sellerclaw describe <operation_id>`; discover ops via `sellerclaw list-operations --tag facebook-ads`.
