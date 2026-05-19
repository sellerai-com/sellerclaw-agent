---
name: google-ads-api
description: "Operate Google Ads on the connected account via the `sellerclaw` CLI. Default path: high-level `launch-search-campaign-for-product` / `launch-pmax-campaign-for-product` — one call per task, copy + assets auto-derived from the SellerClaw product. Use the expert primitives (`create-campaign`, `add-keywords`, `add-asset-group-asset`, …) only when the default path can't express what the supervisor wants. Customer/Merchant credentials resolve server-side — never pass them. For workflow templates use `campaign-playbook`; for product / store context use `catalog` and `sales-channels`."
---

# Google Ads (via `sellerclaw` CLI)

All commands are subcommands of `sellerclaw google-ads …`. Output is JSON on stdout; structured errors on stderr with exit codes (1=user/api, 2=server/network, 3=auth). Credentials resolve server-side from the connected integration — **do not pass tokens, customer ids, or merchant ids**.

**Conventions.** Money is float in account currency (min `0.01`); dates are `YYYY-MM-DD`; bodies are JSON via `-b '<inline>'`, `-b @file.json`, or `-b @-` (stdin). Campaign types supported: `SEARCH`, `SHOPPING`, `PERFORMANCE_MAX`. All mutating endpoints are rate-limited and audit-logged. New campaigns and ad groups are **always paused** server-side; activate explicitly via `patch-campaign` after supervisor approval.

---

## ▶ Launch a campaign — DEFAULT PATH (one call)

Use these two ops by default. They take a SellerClaw `product_id` and a few intent flags, auto-derive every Google Ads field (RSA copy, asset group, keywords, geo, language) from the product's catalog data, and assemble + submit the whole campaign in a single call.

### Search campaign (Google Search results page)

```bash
sellerclaw google-ads launch-search-campaign-for-product -b '{
  "product_id": "<sellerclaw product UUID>",
  "daily_budget": 20.0,
  "country": "US",
  "objective": "traffic"
}'
```

| Field | Required | Default | Notes |
|---|---|---|---|
| `product_id` | ✓ | — | SellerClaw product UUID. Drives headlines / descriptions / keywords. |
| `daily_budget` | ✓ | — | Account-currency major units. |
| `country` | — | `"US"` | ISO code (`US`, `GB`, `DE`), English name (`United States`), or raw `geoTargetConstants/N`. |
| `language` | — | user's `preferred_language` | ISO 639-1 (`en`, `es`) or English name (`English`). |
| `objective` | — | `"traffic"` | `"traffic"` → MAXIMIZE_CLICKS (no conversion tracking needed). `"sales"` → MAXIMIZE_CONVERSIONS (needs a conversion action set up in Google Ads). |
| `final_url` | — | first published Shopify storefront | Pass explicitly if the product isn't published yet, otherwise launch fails with `final_url_unresolvable`. |
| `name` | — | `"Search: <product name>"` | Campaign name shown in Google Ads. |
| `keywords` | — | auto-derived | Override with explicit list. Syntax: `text` = BROAD, `"text"` = PHRASE, `[text]` = EXACT. |
| `negative_keywords` | — | `[]` | Extra campaign-level negatives (same syntax). |
| `cpc_bid` | — | none | Optional manual CPC ceiling for the ad group. |

### Performance Max campaign (Search + Display + YouTube + Discover)

Requires two brand-level inputs the catalog doesn't store: `business_name` (≤25 chars) and `logo_url` (square 1:1, ≥128×128 px). Headlines, long headlines, descriptions, marketing images and CTA are auto-derived; the first `product.images[0]` is used as MARKETING_IMAGE (1.91:1) and `product.images[1]` (or `[0]` if there's only one) as SQUARE_MARKETING_IMAGE (1:1).

```bash
sellerclaw google-ads launch-pmax-campaign-for-product -b '{
  "product_id": "<sellerclaw product UUID>",
  "daily_budget": 50.0,
  "country": "US",
  "business_name": "Acme Shop",
  "logo_url": "https://cdn.example.com/logo-square.png"
}'
```

| Field | Required | Default | Notes |
|---|---|---|---|
| `product_id` | ✓ | — | Product must have ≥1 image (else `product_has_no_images`). |
| `daily_budget` | ✓ | — | PMax practical floor is ~$5–10/day. |
| `business_name` | ✓ | — | ≤25 chars. Shown alongside the ad. |
| `logo_url` | ✓ | — | Square 1:1, ≥128×128 px, ≤5 MB, HTTPS. |
| `country` | — | `"US"` | ISO / name / `geoTargetConstants/N`. |
| `language` | — | user's `preferred_language` | ISO / name. |
| `final_url` | — | first published Shopify storefront | Pass explicitly if product isn't published. |
| `call_to_action` | — | `"SHOP_NOW"` | One of: `SHOP_NOW`, `LEARN_MORE`, `SIGN_UP`, `BUY_NOW`, `BOOK_NOW`, `CONTACT_US`, `DOWNLOAD`, `GET_QUOTE`, `SUBSCRIBE`, `APPLY_NOW`. |
| `square_image_url` | — | `product.images[1]` else `[0]` | Explicit override for the SQUARE_MARKETING_IMAGE slot. |
| `name` | — | `"PMax: <product name>"` | Campaign name. |

**After either launch:** the campaign is `PAUSED`. Activate via `patch-campaign <id> -b '{"status":"ENABLED"}'` after supervisor approval. PMax then needs ~14 days of learning before optimization is reliable.

---

## Browse / inspect

| Intent | Command |
|---|---|
| List campaigns | `sellerclaw google-ads get-campaigns [--status ENABLED\|PAUSED\|REMOVED] [--type SEARCH\|SHOPPING\|PERFORMANCE_MAX] [--limit 50]` |
| One campaign | `sellerclaw google-ads get-campaign <campaign_id>` |
| Ad groups in campaign | `sellerclaw google-ads get-campaign-groups <campaign_id>` |
| Asset groups (PMax) | `sellerclaw google-ads get-asset-groups <campaign_id>` |
| Assets inside an asset group | `sellerclaw google-ads list-asset-group-assets <asset_group_id>` |
| Keywords in an ad group | `sellerclaw google-ads list-ad-group-keywords <adgroup_id> [--status …] [--polarity positive\|negative\|all]` |
| Merchant Center inventory | `sellerclaw google-ads get-products` |
| Optimization recommendations | `sellerclaw google-ads get-recommendations` |
| Action log (audit trail) | `sellerclaw google-ads get-action-log [--entity-id <id>] [--days 1..90]` |

`REMOVED` campaigns are read-only. "Recreate this campaign" = relaunch with the launch ops above; don't copy a removed entity's fields verbatim.

---

## Metrics

`sellerclaw google-ads get-metrics [--level campaign|ad_group|product_group] [--ids c1,c2] [--date-from YYYY-MM-DD] [--date-to YYYY-MM-DD] [--breakdown none|day|device]`

Defaults: last 7 days, `level=campaign`, `breakdown=none`. Always include the resolved date range when reporting numbers. Row fields: `spend`, `impressions`, `clicks`, `ctr`, `avg_cpc`, `conversions`, `conversion_value`, `cpa`, `roas`. Full schema in `references/data-model.md`.

---

## Adjust a live campaign

| Intent | Command | Notes |
|---|---|---|
| Pause / activate | `sellerclaw google-ads patch-campaign <id> -b '{"status":"PAUSED"\|"ENABLED"}'` | Server-enforced. |
| Change budget | `sellerclaw google-ads patch-campaign <id> -b '{"daily_budget": 30.0}'` | ±20% per call cap. Capped also at the account's `max_daily_budget`. |
| Change bidding | `sellerclaw google-ads patch-campaign <id> -b '{"bidding_strategy":"TARGET_ROAS","target_roas":4.0}'` | Use only after enough conversion history. |
| Patch ad group | `sellerclaw google-ads patch-group <adgroup_id> -b '<delta>'` | Patchable: `name`, `status`, `cpc_bid`. |
| Patch PMax asset group | `sellerclaw google-ads patch-asset-group <asset_group_id> -b '<delta>'` | Patchable: `name`, `status`, `final_url`. For content edits use the asset-add/remove ops below. |

---

## Tune keywords (SEARCH only)

| Intent | Command |
|---|---|
| Add positive keywords | `sellerclaw google-ads add-keywords -b '{"ad_group_id":"<id>","keywords":["running shoes","\"trail runner\"","[marathon shoes]"]}'` |
| Add negative keywords (campaign) | `sellerclaw google-ads add-negative-keywords -b '{"campaign_id":"<id>","keywords":["[free]","cheap"]}'` |
| Add negative keywords (ad group) | `sellerclaw google-ads add-negative-keywords -b '{"ad_group_id":"<id>","keywords":["refund"]}'` |
| Remove keywords | `sellerclaw google-ads remove-keywords --resource-names "customers/.../adGroupCriteria/A~B,customers/.../campaignCriteria/C"` |
| Keyword ideas (research) | `sellerclaw google-ads post-keyword-ideas -b '{"keywords":["led lamp"]}'` |

Match-type syntax in every body: `text` = BROAD, `"text"` = PHRASE, `[text]` = EXACT. To remove, pass the criterion's full resource name (returned by `list-ad-group-keywords` as `resource_name`).

---

## Tune PMax asset groups

Use these when the supervisor asks to top up a missing PMax asset type or swap an image without rebuilding the campaign.

| Intent | Command |
|---|---|
| Add one asset | `sellerclaw google-ads add-asset-group-asset <asset_group_id> -b '{"field_type":"HEADLINE","text":"Buy Now!"}'` |
| Remove asset(s) | `sellerclaw google-ads remove-asset-group-assets <asset_group_id> --resource-names "customers/.../assetGroupAssets/X~Y~HEADLINE"` |

`field_type` ∈ `HEADLINE`, `LONG_HEADLINE`, `DESCRIPTION`, `BUSINESS_NAME`, `MARKETING_IMAGE`, `SQUARE_MARKETING_IMAGE`, `PORTRAIT_MARKETING_IMAGE`, `LOGO`, `LANDSCAPE_LOGO`, `YOUTUBE_VIDEO`, `CALL_TO_ACTION_SELECTION`. Required body keys per type: text fields → `text`; image fields → `image_url`; `YOUTUBE_VIDEO` → `youtube_video_id`; `CALL_TO_ACTION_SELECTION` → `call_to_action`. Google enforces minimum-asset rules — removing the last HEADLINE / LONG_HEADLINE / etc. will fail with `CANNOT_REMOVE_*`.

---

## Expert / advanced — raw `create-campaign`

Reach for this **only** when a launch op can't express what the supervisor needs (e.g. multiple ad variants in one ad group, custom RSA copy that differs from the product, a Shopping campaign for a Merchant Center catalog instead of a single product).

`sellerclaw google-ads create-campaign -b '<json>'` — Required body: `name`, `type` (`SEARCH` / `SHOPPING` / `PERFORMANCE_MAX`), `daily_budget`, `bidding_strategy`. Per-type extras:

- **SEARCH** — `ad_group: {name, cpc_bid?, keywords[], responsive_search_ad: {headlines[3..15] (≤30c), descriptions[2..4] (≤90c), final_url, path1?, path2?}}`.
- **SHOPPING** — `merchant_id?` (falls back to the connected Merchant Center). Auto-bidding only (no `MANUAL_CPC`/`MAXIMIZE_CLICKS`).
- **PERFORMANCE_MAX** — `asset_group` with `business_name` (≤25c), `final_url`, `headlines[≥3]` (≤30c), `long_headlines[≥1]` (≤90c), `descriptions[≥2]` (≤90c), `image_urls[≥1]` (1.91:1, ≥600×314 px), `square_image_urls[≥1]` (1:1, ≥300×300 px), `logo_urls[≥1]` (1:1, ≥128×128 px). Optional: `portrait_image_urls`, `landscape_logo_urls`, `youtube_video_ids`, `call_to_action`. Auto-bidding only.

Optional for all types: `geo_target_constants` (raw IDs or `geoTargetConstants/N`), `language_constants`, `negative_keywords`.

`bidding_strategy` values: `MAXIMIZE_CONVERSIONS`, `MAXIMIZE_CONVERSION_VALUE` (+optional `target_roas`), `TARGET_ROAS` (requires `target_roas`), `TARGET_CPA` (requires `target_cpa`), `MAXIMIZE_CLICKS`, `MANUAL_CPC`, `TARGET_SPEND`. SHOPPING/PMAX accept only the auto-bidding subset.

If the proxy rejects the body, the error code (`pmax_business_name_missing`, `pmax_long_headlines_missing`, `pmax_square_images_missing`, `rsa_headlines_count_invalid`, `bidding_strategy_incompatible_with_type`, …) tells you the exact field to fix.

---

## Guardrails

- **Launch ops are the default** — reach for `create-campaign` only when they can't express the task.
- Mutating endpoints are server-side rate-limited; don't burst-update. Retry a failed call at most twice; then return a blocker.
- All new campaigns / ad groups are forced to `PAUSED`. Activate only after supervisor approval.
- Budget patches capped at ±20% per call.
- PMax needs ~14 days of learning before optimization changes are meaningful.
- Daily budget min `$0.01`; PMax practical floor is `3 × target_cpa` (default `$45`).
- Never echo or log auth tokens, customer ids, or merchant ids — they resolve server-side.
- Always include the resolved date range when reporting metrics.

---

## Reference

- Full data models — `references/data-model.md`.
- Discover ops live — `sellerclaw list-operations --tag google-ads`. Describe one — `sellerclaw describe <operation_id>`.
