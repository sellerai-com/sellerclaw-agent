---
name: ebay-products
description: "Work with eBay listings and sellable inventory only—browse or search active listings, sync SKU quantities on offers. Use whenever the task is about eBay listing catalog, SKUs on listings, or stock/quantity sync on listings—even if they do not say ebay-products."
---

# eBay products

**Assumption:** `**store_id`** (sales-channel UUID for the target eBay channel) is **already known** from the task or session context. If it is missing or ambiguous, resolve it.

## Task: list or review current listings

**Goal:** See what is live on the channel (titles, SKUs, qty, price, status).

**Algorithm**

1. Run `sellerclaw ebay-listings list <store_id>`; increase `--page-size` when the catalog is large (within the limit shown in `ebay-listings list --help`).
2. If the task needs **more rows than one response**, paginate only if the operation documents a cursor/token in `ebay-listings list --help`; otherwise report truncation and what is missing.
3. Summarize counts and highlight rows that match the task (e.g. by `**sku`**, `**remote_id**`, status fields returned in `**items**`).
4. Interpret listing rows using field names present in stdout (`items` objects are loosely typed).

---

## Task: find listings matching criteria

**Goal:** Narrow to specific SKUs, titles, item ids, or filters — not a full browse.

**Algorithm**

1. Build `--json-body` for `**search`** from the task: `**search_type**` (`sku` or `remote_id`) and optional `**search_values**`.
2. Run `sellerclaw ebay-listings search <store_id> --json-body '<json>'` (or `@file.json`).
3. If zero results, confirm whether the criteria are too strict vs. a data lag; optionally cross-check with a **limited** `list` if the task allows.
4. Return matched `**remote_id`** / internal ids / `**sku**` for any follow-up work.

---

## Task: sync stock (quantities) to eBay

**Goal:** Push quantity updates from the task payload (or “all known SKUs”) onto eBay listings.

**Algorithm**

1. Build `--json-body` with an `**items`** array (`sku`, `quantity`, optional disambiguators) from the task payload.
2. Run `sellerclaw ebay-listings sync-stock <store_id> --json-body '...'`.
3. Parse `**results**` and `**errors**` (`EbayStockSyncResponse`). If any `**errors**` rows exist, return `**partial**` or `**failed**` per severity; surface each failure payload; do not hide failed SKUs.
4. If the task gives SKUs that do not appear in the response, note they may not map to listings yet.

---

## Reference

**Wire models** (listing/search/sync payloads): `**references/data-models.md`**.
*OpenAPI:** full JSON Schema for any operation — `**sellerclaw describe <operation_id>*`*; discover `**operation_id**` via `**ebay-listings … --help**` or `**sellerclaw list-operations --tag ebay-listings**`. Use when the wire reference above or `**--help**` is not enough.