---
name: shopify-products
description: "Manage Shopify products and listings on the connected Shopify store: search what is live, publish an existing SellerClaw catalog product, edit / sync stock / price / title, toggle visibility, or delete. Use when the delegated task says 'publish on Shopify', 'put this on Shopify', 'update the Shopify listing', 'change stock/price/title', 'hide/unhide the product', 'delete from Shopify', 'what's live on Shopify', or any task referring to a Shopify SKU, variant, listing visibility, stock, or pricing. For non-product storefront content (pages, menus, theme) use `shopify-storefront-setup`."
---

# Shopify products

**Assumption:** `store_id` (sales-channel UUID for the target Shopify shop) is **already known** from the task or session context. If it is missing or ambiguous, resolve it first.

## Scope

This skill **cannot create products from scratch.** New product records require **supplier binding** and are produced by other agents/skills (catalog / supplier side). What this skill can do:

- **Read** what is on the Shopify storefront (overview / specific lookup).
- **Publish** an **already-existing internal SellerClaw catalog product** (i.e. a row already supplier-bound in our DB) as a Shopify listing.
- **Edit / sync / delete** Shopify products that already exist on Shopify (stock, price, attributes, visibility).

If a task asks for a brand-new product that does not yet have an internal catalog row, **stop and route to the catalog/supplier skill** — do not bypass this rule with raw Shopify create commands.

Pick the section by **task intent** below.

---

## If you need a summary / overview of products on the store

**When:** "what's on the store", "show me the catalog", browsing without a specific id.

**Command:** `sellerclaw stores list-listings <store_id> [--status active|draft|archived] [--limit N]`

**CLI options:**

- `<store_id>` (required, positional) — sales-channel UUID.
- `--status` (optional, default `active`) — filter by Shopify product status.
- `--limit` (optional) — cap rows returned.

**Per-row fields you read from the response:**

- `sku` — merchant SKU on the variant; the join key for stock/price flows.
- `title` — buyer-facing storefront title.
- `price` / `currency` — current sell price (decimal string) and ISO currency.
- `quantity` — sellable qty exposed by the channel right now.
- `remote_id` — Shopify **variant id** for this row (use as `remote_id` in `sync-stock`).
- `url` — storefront permalink (or `null` when no Online Store URL).
- `image_url` — main image URL or `null`.
- `is_variant` — `true` for non-default variant lines (explains duplicate titles/SKUs).

**Algorithm:**

1. Run the command; raise `--limit` if the catalog is large.
2. Summarize counts and call out gaps (`null` URLs, missing images, zero qty on active rows).
3. For text-narrowed sweeps, switch to *specific products* below.

---

## If you need info on specific products (identifiers known)

**When:** SKU, Shopify variant id, or Shopify product id is already known and you want details on those rows.

### Default — listing-shaped projection

**Command:** `sellerclaw stores search-listings <store_id> --type {sku|title|remote_id} --q <value>` (see `--help` for the full `--type` list).

**CLI options:**

- `<store_id>` (required, positional).
- `--type` (required) — what `--q` is matched against: `sku` for merchant SKU, `remote_id` for Shopify **variant id**, `title` for fragment search.
- `--q` (required) — value to match.

**Response shape:** array of search buckets; each has `search_type` + `query` (echo of inputs) and `listings` — same per-row fields as in *summary* above (`sku`, `title`, `price`, `currency`, `quantity`, `remote_id`, `url`, …).

### Fallback — raw Shopify Admin fields

When the listing-shaped projection lacks what you need (metafields, full media set, options matrix, raw status, inventory locations, etc.):

**Command family:** `sellerclaw stores proxy-shopify-* …` — passthrough to Shopify Admin REST. Response field names follow Shopify's REST reference for the resource path you address, **not** a SellerClaw wrapper.

### When you only have an **internal** catalog `product_id`

The internal ↔ Shopify mapping lives in **draft listings** — see *publish existing catalog products* below; `list-draft-listings` exposes both ids.

---

## If you need to sync stock and/or prices for known SKUs

**When:** "set quantity X for SKU Y", "reprice these SKUs", inventory push.

**Command:** `sellerclaw stores sync-stock <store_id> --json-body '{"items":[…]}'` (or `**--json-body @file.json**` for large payloads).

**Body parameters:**

- `items` (required, array, ≥ 1) — lines to reconcile. Per item:
  - `sku` (required) — variant SKU on Shopify.
  - `quantity` (required, integer ≥ 0) — target **available** quantity after sync.
  - `remote_id` (optional) — exact Shopify **variant id**; use when the SKU is reused/ambiguous.
  - `price` (optional, decimal string) — new sell price in shop money.
  - `compare_at_price` (optional, decimal string) — "was"/strikethrough price; pair with `price` for discount display.

**Algorithm:**

1. Build `items` with at least `sku` + `quantity`; add `remote_id` only for collision/disambiguation; add `price`/`compare_at_price` to reprice in the same call.
2. Run the command.
3. Read response: `results` = per-line successes (opaque), `errors` = per-line failures with SKU + message — surface every failed SKU, never hide them.

> Use this instead of `update-shopify-products` for pure stock/price edits — it is keyed by SKU and is the cheap path.

---

## If you need to publish an existing internal catalog product as a Shopify listing

**When:** an internal SellerClaw catalog product (already in DB, already supplier-bound) needs to appear as a live Shopify product.

**Two sequential commands** — drafts first, then publish.

### Step 1 — create draft listing rows

**Command:** `sellerclaw stores create-draft-listings <store_id> --json-body '{"product_ids":["…"], "product_type": null}'`

**Body parameters:**

- `product_ids` (required, array of string UUIDs) — **internal SellerClaw catalog product ids** that should gain a draft listing for this Shopify channel. **Must already exist in our DB.** If a caller gives you something that doesn't, stop and route to the catalog skill.
- `product_type` (optional, string, default `null`) — Shopify `product_type` to force on the draft (taxonomy override). `null` = use default.

**Response per item:** `results[].listing` with:

- `id` — internal **draft listing UUID** — feed into Step 2.
- `product_id` — internal catalog product backing the listing.
- `status` — workflow phase (draft / publishing / live / error).
- `shopify_product_id` — `null` until Step 2 succeeds.
- `**variants[]**` — internal ↔ Shopify variant mapping; `sku`, `sell_price`, `shopify_variant_id` (`null` while draft-only).

Plus `**errors[]**` (per failed input) with `message`, `key` (input fingerprint), `details`.

### Step 2 — (optional) inspect draft state

**Command:** `sellerclaw stores list-draft-listings <store_id>` — see `--help` for filters (status, etc.). Use to pick which drafts to publish or to debug stuck rows.

### Step 3 — publish drafts to Shopify

**Command:** `sellerclaw stores publish-draft-listings <store_id> --json-body '{"listing_ids":[…]}'`

**Body parameters:**

- `listing_ids` (required, array of UUIDs) — internal **draft listing ids** from Step 1 / Step 2.

**Response:** same shape as Step 1 — each successful row now has a non-null `shopify_product_id` and `**variants[].shopify_variant_id**`. Surface any `**errors[]**` rows.

**Reporting:** internal `listing.id` → resulting `shopify_product_id` per row, plus failures.

---

## If you need to edit existing Shopify products in bulk

**When:** patch attributes (title, body, status, variants, media, tags) on products **already existing on Shopify** — not stock/price (use *sync-stock* for those).

**Command:** `sellerclaw stores update-shopify-products <store_id> --json-body '{"items":[…]}'` (use `**@file**` for large payloads).

**Body parameters:**

- `items` (required, array) — one row per Shopify product. Per row, `product_id` is the only required key; everything else is a delta (omit fields you do not change; `null` / empty array means "clear" per Shopify patch rules):
  - `product_id` (required) — Shopify Admin **product id** to mutate.
  - `title`, `body_html` — storefront title / HTML description.
  - `vendor`, `product_type`, `tags` — taxonomy / filtering metadata.
  - `status` — `draft` / `active` / `archived`.
  - `images` — array of absolute URLs to import into Shopify's gallery.
  - `variants` (array) — per-variant patch:
    - `variant_id` (required to target one variant when the product has multiple).
    - `sku`, `title`, `barcode` — identifiers and labels.
    - `price`, `compare_at_price` — selling vs strike price.
    - `meta` — supplier linkage block (`supplier_variant_id`, `supplier_product_id`, `cost_price`) — internal, not buyer-visible.

**Algorithm:**

1. Resolve Shopify `product_id` per target row (via `list-listings` / `search-listings` / `**proxy-shopify-***`).
2. Build `items` with only the deltas.
3. Run the command.
4. Return per-product success/failure summary.

---

## If you need to hard-delete Shopify products

**When:** "remove these products from Shopify". **Irreversible** on Shopify side for that product record.

**Command:** `sellerclaw stores delete-shopify-products <store_id> --json-body '{"product_ids":[…]}'`

**Body parameters:**

- `product_ids` (required, array) — Shopify Admin product ids to delete.

**Algorithm:**

1. Resolve Shopify `product_id`s.
2. Run the command.
3. Report deleted vs failed product ids.

---

## If you need to toggle storefront visibility of existing Shopify products

**When:** the products already exist in Shopify and you only need to switch them on/off across publications (Online Store, sales channels, apps). **Not** the same as the publish flow above — this changes visibility, it does not create or link new products.

**Commands:** `sellerclaw stores publish-shopify-products <store_id> --json-body '…'` / `unpublish-shopify-products <store_id> --json-body '…'`

**Body parameters (same shape for both):**

- `product_ids` (required, array) — Shopify Admin product ids to toggle.
- `publication_names` (optional, array of strings or `null`) — `null` → default publications for the shop; otherwise limit to named publication handles (e.g. `"online_store"`).

**Algorithm:**

1. Resolve Shopify `product_id`s to toggle.
2. Run publish or unpublish with the body above.
3. Empty response body is normal — trust HTTP 200 / CLI exit code.

---

## Reference

**Wire models** (full schema for listings, drafts, batch products, stock sync, including rare optional fields): `references/data-model.md`.

**OpenAPI:** definitive JSON Schema for any operation — `sellerclaw describe <operation_id>` (discover ids via `sellerclaw list-operations`, tag `stores`). Use when this skill and `--help` are not enough.