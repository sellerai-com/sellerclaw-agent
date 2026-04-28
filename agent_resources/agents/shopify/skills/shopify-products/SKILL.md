---
name: shopify-products
description: "Handle Shopify products and storefront listings only—browse or search live listings, sync inventory and prices per SKU, batch Admin product operations, publish catalog drafts as listings. Use when the task is clearly about sellable items: SKUs, variants, what is listed, listing visibility, shelf stock or pricing, bulk product edits, or drafts going live—even without naming this skill."
---

# Shopify products

**Assumption:** `**store_id`** (sales-channel UUID for the target Shopify shop) is **already known** from the task or session context. If it is missing or ambiguous, resolve it.

## Task: inspect live storefront listings

1. `**sellerclaw stores list-listings <store_id>`** — optional `--status` (default active), `--limit`.
2. If the task implies **text search** (title/SKU fragment), use `**sellerclaw stores search-listings <store_id> --type … --q …`** (see `**--help**` for required flags).
3. Summarize using listing fields returned for each row—`**sku**`, `**title**`, `**price**`, `**currency**`, `**quantity**`, `**url**`, `**remote_id**`—and call out gaps (`**null**` URLs, missing images).

---

## Task: sync stock and prices

1. Build `**items**` with at least `**sku**` + `**quantity**`; add `**remote_id**`, `**price**`, `**compare_at_price**` when the task calls for repricing or variant pinning.
2. `**sellerclaw stores sync-stock <store_id> --json-body '{…}'**`.
3. Report outcome: counts updated vs failures; if the API returns per-SKU errors, list affected SKUs and messages.

---

## Task: batch products (create / update / delete / publish / unpublish)

1. Shape `**--json-body**` from the task (typed `**sellerclaw stores …**` commands); use `**--json-body @file**` when the payload is large.
2. Build `**items**` (or `**product_ids**` for delete/publish/unpublish) from the task; never invent fields — omit unknown optional keys.
3. Execute one of:
  - `**sellerclaw stores create-shopify-products <store_id> --json-body '…'**`
  - `**sellerclaw stores update-shopify-products …**`
  - `**sellerclaw stores delete-shopify-products …**`
  - `**sellerclaw stores publish-shopify-products …**` / `**unpublish-shopify-products …**`
4. Return structured summary: product identifiers touched, failures with reasons.

---

## Task: draft listings → publish to Shopify

1. `**sellerclaw stores create-draft-listings <store_id> --json-body '{"product_ids":["…"],"product_type":null}'**` — creates/links draft listing rows (`ShopifyListingResponse`-shaped results per item).
2. Inspect state if needed: `**sellerclaw stores list-draft-listings <store_id>**` (optional filters per `**--help**`).
3. `**sellerclaw stores publish-draft-listings <store_id> --json-body '…'**` — `**listing_ids**` batch per publish workflow.
4. Report listing IDs, product IDs, and any batch errors.

---

## Reference

**Wire models** (listings, drafts, batch products, stock sync): `**references/data-model.md`**.

**OpenAPI:** full JSON Schema for any operation — `**sellerclaw describe <operation_id>`** (discover ids via `**sellerclaw list-operations**` — `**stores**` tag). Use when the wire reference above or `**--help**` is not enough.