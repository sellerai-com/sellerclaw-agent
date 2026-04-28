---
name: ebay-products
description: "Manage eBay listings: search what is live, draft from existing catalog products, publish, edit and sync price / qty / title, withdraw, relist, or delete. Use whenever the task is about an eBay listing — SKUs, drafts, publishing, withdrawal, visibility, stock, or pricing on offers."
---

# eBay products

**Assumption:** `**store_id`** (sales-channel UUID for the target eBay channel) is **already known** from the task or session context. If it is missing or ambiguous, resolve it first.

## Scope

This skill **cannot create catalog products from scratch.** New product records require **supplier binding** and are produced by other agents/skills (catalog / supplier side). What this skill can do:

- **Read** live eBay state (overview / specific lookup, search by sku/remote_id).
- **Sync stock and price** on offers that already exist.
- **Create drafts** locally for an **already-existing internal SellerClaw catalog product** (i.e. a row already supplier-bound in our DB) so it can be pushed to eBay.
- **Edit and delete drafts** while they are local (have not been pushed to eBay yet).
- **Publish** drafts to eBay (or relist a previously-withdrawn listing).
- **Sync-update** a published listing — edits flow through to eBay.
- **Withdraw** a listing from sale (keeps eBay-side ids for later relist).
- **Terminally delete** a listing (cleans up eBay-side artifacts and our DB row).

If a task asks for a brand-new product that does not yet have an internal catalog row, **stop and route to the catalog/supplier skill** — do not bypass this rule with raw eBay create commands.

### State machine

Each local listing is in exactly one of three states. Pick the right command family by state:

- `**DRAFT`** — only in our DB, never pushed to eBay. Edit/delete via the **draft** commands; `**publish**` to push for the first time.
- `**PUBLISHED**` — live on eBay. Edits sync through (via the **sync-update** command); plain draft-mutation refuses with 409. `**withdraw**` to take down without losing eBay-side ids.
- `**WITHDRAWN`** — ended on eBay but eBay-side ids preserved so we can relist. `**publish`** again to relist; **terminal delete** to also wipe eBay artifacts.

Pick the section by **task intent** below.

---

## If you need a summary / overview of listings live on eBay

**When:** "what's live on eBay", "show the current catalog", browsing without a specific id.

**Command:** `**sellerclaw ebay-listings list <store_id> [--page-size N]`**

**CLI options:**

- `**<store_id>`** (required, positional).
- `**--page-size**` (optional, default `200`, range `1–200`).

**Per-row fields you read:** `**sku`** (seller SKU), `**remote_id`** (eBay listing/item id), `**title`**, `**price`** + `**currency**`, `**quantity**`, listing state, image URL, `**url`** (storefront permalink).

**Algorithm:**

1. Run the command; raise `**--page-size`** for large catalogs.
2. Summarize counts and call out gaps (zero qty on active rows, missing images).
3. For id-narrowed sweeps, switch to *specific listings* below.

---

## If you need info on specific listings (identifiers known)

**When:** SKU(s) or eBay listing/item id(s) are known and you want the matching live rows.

**Command:** `**sellerclaw ebay-listings search <store_id> --json-body '<json>'`** (or `**@file.json**`).

**Body parameters:**

- `**search_type`** (required) — `**sku**` or `**remote_id**`.
- `**search_values`** (required, array of strings) — values to look up in the chosen mode.

**Response:** `**results[]`** — same loose row shape as `list` (`**sku**`, `**remote_id**`, `**title**`, `**price**`, `**quantity**`, …) for matched rows.

---

## If you need to sync stock and/or prices on existing offers (by SKU)

**When:** "set quantity X for SKU Y", "reprice these SKUs", inventory push.

**Command:** `**sellerclaw ebay-listings sync-stock <store_id> --json-body '{"items":[…]}'`** (or `**--json-body @file.json`** for large payloads).

**Body parameters:**

- `**items`** (required, array, ≥ 1):
  - `**sku**` (required) — seller SKU on the offer.
  - `**quantity**` (required, integer ≥ 0) — target **available** quantity.
  - `**remote_id`** (optional) — eBay listing id; use when the same SKU appears on multiple offers.
  - `**item_id`** (optional) — eBay item id for further marketplace-side disambiguation.

**Response:** `**results`** = applied rows, `**errors`** = per-line failures with the SKU + reason. Surface every failed SKU — never hide them. Use this command for pure stock/price edits (it is keyed by SKU and the cheap path).

---

## If you need to **create** a draft listing for an existing internal catalog product

**When:** an internal SellerClaw catalog product (already in DB, already supplier-bound) needs to gain an eBay listing — start with a **local draft**, then publish in a separate step.

**Command:** `**sellerclaw stores create-ebay-draft-listings <store_id> --json-body '<json>'`**

**Body parameters:**

- `**product_ids`** (required, array of string UUIDs, ≥ 1) — **internal catalog product ids**. Must already exist in our DB. If a caller gives you something that doesn't, stop and route to the catalog skill.
- `**api_kind`** (optional, string, default `**"trading"**`) — which eBay API to use on publish:
  - `**"trading"**` (default, simpler) — single `**AddFixedPriceItem`** call covers single + multi-variant listings.
  - `**"inventory"**` — Sell Inventory API flow (separate inventory_item + offer + group steps); use when the task explicitly requires it.
- `**title**` (required, ≤ 80 chars) — listing title.
- `**description**` (optional, ≤ 500_000 chars) — listing description (HTML allowed).
- `**category_id**` (required) — eBay leaf category id (must be a leaf and, for multi-variant listings, must support variations).
- `**condition`** (required) — `**"NEW"`** / `**"USED"`** / `**"REFURBISHED"`**.
- `**merchant_location_key`** (required) — inventory location key (often `**sellerclaw_fbz**` for SellerClaw FBZ).
- `**fulfillment_policy_id`**, `**payment_policy_id`**, `**return_policy_id**` (required) — seller business-policy ids.
- `**images`** (required, array of URLs, ≤ 24).
- `**aspects`** (required, dict of `name → list[str]`) — eBay item specifics for the leaf category (e.g. `{"Brand": ["Acme"], "Color": ["Red"]}`). Group-level aspects must include any item-specific the category marks as required.
- `**sell_prices`** (optional, dict `supplier_variant_id → decimal string`) — override the auto-computed sell price per variation; default uses sales-channel margin × supplier cost.

**Response per item:** `**results[].listing`** with:

- `**id`** — internal **draft listing UUID** (use as `**listing_id**` in publish / update / withdraw / delete commands).
- `**product_id`** — the internal catalog product backing the listing.
- `**status`** — `**"draft"**` immediately after creation.
- `**ebay_item_id`** — `null` until publish (Trading API path).
- `**inventory_item_group_key`** — `null` until publish (Inventory API multi-variant path).
- `**variants[]`** — internal ↔ eBay variant mapping; `**sku**`, `**sell_price**`, `**quantity**`, `**ebay_offer_id**`, `**ebay_listing_id**` (`null` while in DRAFT).

Plus `**errors[]`** (per failed input) with `**message**`, `**key**` (input fingerprint), `**details**`.

> Multi-variant: if the internal product has ≥ 2 variations, the draft already represents the variant set. Use a leaf `**category_id`** that supports variations (eBay errorId 25005 otherwise).

---

## If you need to **inspect** local listings (DB state, including drafts and published)

**When:** you have an internal listing UUID, or want to see all listings for the store regardless of marketplace state.

**List:** `**sellerclaw stores list-ebay-draft-listings <store_id> [--status draft|published|withdrawn]`**

**Single:** `**sellerclaw stores get-ebay-draft-listing <store_id> <listing_id>`**

**Per-row fields you care about:** `**id**`, `**product_id**`, `**status**`, `**title**`, `**category_id**`, `**condition**`, `**ebay_item_id`** (Trading), `**inventory_item_group_key**` (Inventory multi-variant), `**variants[]**` (with `**sku`**, `**sell_price**`, `**quantity**`, `**ebay_offer_id**`, `**ebay_listing_id**`), `**created_at`** / `**updated_at`**.

> Despite the name, `**list-ebay-draft-listings**` returns rows in **all** statuses — filter via `**--status`** when needed. The `draft` in the URL just signals "the local DB resource", not the listing state.

---

## If you need to edit a draft locally (no eBay roundtrip)

**When:** the listing is still `**DRAFT**` — change title, price overrides, aspects, etc., before pushing.

**Command:** `**sellerclaw stores update-ebay-draft-listing <store_id> <listing_id> --json-body '<json>'`**

**Body parameters** (all optional — PATCH semantics, omit fields you do not change):

- `**title**`, `**description**`, `**category_id**`, `**condition**`,
- `**merchant_location_key**`, `**fulfillment_policy_id**`, `**payment_policy_id**`, `**return_policy_id**`,
- `**images**` (replaces the array), `**aspects**` (replaces the dict).

**Refusal rules:** if the listing is **not** `DRAFT`, the endpoint returns **HTTP 409** with `**code: "listing_not_draft"`**. Use the `update-ebay-listing` command instead (see *sync-update of a published listing* below).

---

## If you need to delete a draft (no eBay roundtrip)

**When:** abandoning a never-published draft.

**Command:** `**sellerclaw stores delete-ebay-draft-listing <store_id> <listing_id>`**

**Refusal rules:** if the listing is `PUBLISHED` or `WITHDRAWN`, returns **HTTP 409** — use the *terminal delete* command below, which will clean up eBay-side artifacts first.

---

## If you need to **publish** drafts to eBay (or relist a withdrawn listing)

**When:** a `DRAFT` should appear live on eBay, or a `WITHDRAWN` listing should be relisted.

**Command:** `**sellerclaw stores publish-ebay-listings <store_id> --json-body '{"listing_ids":[…]}'`**

**Body parameters:**

- `**listing_ids`** (required, array of UUIDs, ≥ 1) — **internal listing ids** (from create / list / get above).

**Behavior by current status:**

- `**DRAFT → PUBLISHED**` — first-time push. For Trading-API drafts: one `**AddFixedPriceItem`** call returns an `**ebay_item_id**`. For Inventory-API drafts: `**bulk_create_or_replace_inventory_item**` + (if multi-variant) `**create_or_replace_inventory_item_group**` + `**bulk_create_offer**` + `**publish_offer**` (single) or `**publish_offer_by_inventory_item_group**` (multi).
- `**WITHDRAWN → PUBLISHED**` — relist using stored ids (`**ebay_item_id**` or `**inventory_item_group_key**` / `**ebay_offer_id**`s).
- `**PUBLISHED**` — no-op (returns the row unchanged).

**Response:** same shape as `create-ebay-draft-listings` — each successful row now has the eBay-side ids populated. Surface any `**errors[]**` rows.

---

## If you need to update a **PUBLISHED** listing (sync change to eBay)

**When:** a non-critical attribute (title, description, images, prices, qty, aspects) changes on an already-live listing.

**Command:** `**sellerclaw stores update-ebay-listing <store_id> <listing_id> --json-body '<json>'`**

**Body parameters:** same shape as `update-ebay-draft-listing` (PATCH semantics, all fields optional).

**Critical-field guard:** changing `**category_id**` or `**condition`** on a PUBLISHED listing returns **HTTP 409** with `**code: "critical_field_change"`**. The fix is: `withdraw-ebay-listings` first, then `update-ebay-draft-listing`, then `publish-ebay-listings`.

**Refusal rules:** if the listing is not `PUBLISHED`, returns **HTTP 409** with `**code: "listing_not_published"`**. For `DRAFT` listings, edit locally and publish; for `WITHDRAWN` listings, edit locally then relist.

---

## If you need to **withdraw** a listing from sale (preserve eBay-side ids for relist)

**When:** "take this off eBay but keep the listing record so we can relist later".

**Command:** `**sellerclaw stores withdraw-ebay-listings <store_id> --json-body '{"listing_ids":[…]}'`**

**Body parameters:**

- `**listing_ids`** (required, array of UUIDs) — **internal listing ids**.

**Behavior:** `PUBLISHED → WITHDRAWN` (eBay-side `EndItem` for Trading or `withdrawOffer` / `withdrawOfferByInventoryItemGroup` for Inventory). On `WITHDRAWN` it is a no-op; on `DRAFT` it returns 409 (nothing to withdraw).

---

## If you need to **terminally delete** a listing (eBay artifacts + DB)

**When:** the listing should disappear permanently; relisting later would have to start from scratch.

**Command:** `**sellerclaw stores delete-ebay-listing <store_id> <listing_id>`**

**Behavior:** works for any status. If `PUBLISHED`, withdraws first; for Inventory-API listings, deletes offers + inventory items + inventory_item_group; for Trading-API listings, the eBay item is left in `Ended` state (eBay does not allow hard delete) but our DB row is removed. Returns **204** on success.

> For DRAFT-only deletion, prefer `delete-ebay-draft-listing` — it skips the eBay roundtrip.

---

## Reference

**Wire models** (full schema for create/update/publish/withdraw payloads, listing snapshot, variant): `**references/data-models.md**`.

**OpenAPI:** definitive JSON Schema for any operation — `**sellerclaw describe <operation_id>`**; discover `**operation_id**` via `**sellerclaw <group> <command> --help**` or `**sellerclaw list-operations`** (tags: `**ebay-listings**` for live read/sync ops, `**stores**` for the draft/publish/withdraw/delete operations).
