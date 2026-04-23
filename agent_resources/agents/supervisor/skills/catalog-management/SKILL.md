---
name: catalog-management
description: "Manage store catalog workflows, including product sourcing, owner approval, publishing, and sync strategy."
---

# Catalog Management Skill

## Goal
Fill stores with quality products at target margin and keep catalog synchronized with supplier data.

## Triggers
- Owner command to find/add products for a store.
- Owner command to run a manual catalog sync for a specific store (exception / investigation).
- System anomaly notification (e.g., out-of-stock / price anomaly) that requires owner-visible action.

## Efficiency Rules (MANDATORY)

- **Search only what was asked.** If owner approved specific products (e.g., "A + B + C + D"), search ONLY for those 4 items — one query per item, `page_size=5`. Do NOT add extra categories, do NOT run broad research.
- **One candidate per approved item.** When items are already approved, pick the top-1 result per search (best price + in stock). Do NOT collect 5+ candidates per category.
- **Verify only what you will publish.** Check variants, stock, and shipping ONLY for the chosen candidate — not for every result in the search.
- **Fail fast.** If a search returns no usable results for an item, report it immediately. Do NOT retry with 10 different query reformulations.
- **Budget your API calls.** For N approved items, the expected call count is: N searches + N variant lookups + N stock checks + N shipping calculations = ~4N calls total. If you exceed 6N calls, you are doing too much — stop and report partial results.

## Workflow: Fast Publish (owner already chose products)

Use this workflow when the owner has already selected specific product types/categories to publish (e.g., "publish A, B, C, D").

### 1. Search (one query per item)
For each approved item, delegate to `supplier`:
> Search 1 best product for "{item_description}". Target country: US. Get the top result's variants, check stock for the first variant, calculate shipping to US. Return 1 candidate.

### 2. Confirm with Owner
Present the 1 candidate per item. Ask for final `approve` / `reject`.

### 3. Save & Publish
After approval:
1) Save products in DB via `POST /products` and collect `product_ids`.
2) Delegate to `shopify` to create listings for `product_ids`, then publish listings.
3) If publication happens on eBay via agent flow, immediately register published variant mapping via:
   - `POST /agent/listing-sync/published`
   - payload: `product_id`, `sales_channel_id`, `variants[{supplier_variant_id, sku, remote_id}]`

## Workflow: Product Discovery and Publication

Use this workflow when the owner asks to "find products" or "fill the store" without specifying exact items.

### 1. Identify Search Criteria
From the store profile (available in AGENTS.md stores table) and basic Shopify shop metadata (`GET /stores/{store_id}/info` with the channel UUID):
- Niche and target audience.
- Categories with keywords.
- Hero products with search terms and price ranges.
- Supplier config (provider, warehouse filter).

### 2. Delegate Product Search to Supplier
For each category/hero product:

> Search products in niche "{niche}". Categories: {categories}. Search terms: {search_terms}. Target country: US. Find at least 5 candidates per category. For each: get variants, check stock, calculate shipping to US. Return candidates scored by quality.

Supplier returns candidates with `source_price`, `shipping_cost`, `score` (quality). Supplier does **not** calculate sell price or margin.

### 3. Review and Present Candidates
Compile results and present to owner (system will calculate the listing price during listing creation based on store margin):

```
⚡ ACTION — Product Candidates

Store: {store_name} — Category: {category}

 # │ Product           │ Cost  │ Ship │ Score
───┼───────────────────┼───────┼──────┼──────
 1 │ Dog Leash Retract │ $4.20 │ $2.8 │ 0.85
 2 │ Nylon Dog Collar  │ $2.10 │ $1.5 │ 0.78
 3 │ Pet Harness Set   │ $6.50 │ $3.2 │ 0.82

approve all / approve 1,2 / reject?
```

### 4. Save Products and Create Listings
After owner approves:
1) Save approved products in DB via `POST /products` (include supplier product/variant IDs, prices, shipping).
2) Delegate to `shopify`:
> Store ID: {store_id} (UUID). Create Shopify listings for products: {product_ids}. Use product.category as product_type unless overridden.
3) Then publish listings:
> Store ID: {store_id} (UUID). Publish Shopify listings: {listing_ids}.
4) If publication happens on eBay via agent flow, immediately register published variant mapping via:
   - `POST /agent/listing-sync/published`
   - payload: `product_id`, `sales_channel_id`, `variants[{supplier_variant_id, sku, remote_id}]`

### 5. Report Results
```
ℹ️ INFO — Listings Published

Store: {store_name}
- Listings created: {N}
- Listings published: {M}
- Errors: {K or "none"}

Listings:
1. {product_name} — Listing ID: {listing_id}
2. ...
```

## Workflow: Catalog Sync (manual investigation only)

> Routine stock/price sync runs automatically. Use this only for owner-requested checks or system-flagged anomalies.

### 1. Collect Current Store Data
Delegate to `shopify`:
> Store ID: {store_id} (UUID). List all active products with: title, sku (from first variant), price, inventory_quantity, metafield custom.supplier_sku. Format: JSON array.

### 2. Collect Current Supplier Data
Extract supplier SKUs from store products and delegate to `supplier`:
> Get current data for supplier products: {product_ids_list}. For each: current price, stock availability, variant stock. Return structured JSON.

### 3. Compare and Identify Changes
- **Price changes**: if supplier source_price changed significantly (>5%), flag for price update.
- **Stock changes**: if supplier stock went to 0, set marketplace quantity to 0 (do not unpublish automatically).
- **Availability**: if product no longer available at supplier, set quantity to 0 and escalate to owner.

### 4. Apply Updates
If changes found, present summary to owner:

```
⚡ ACTION — Sync Results

Store: {store_name}

Price updates needed:
- {product_title}: ${old} → ${new} (supplier cost changed)

Out of stock (quantity will be set to 0):
- {product_title}: supplier stock = 0

approve sync / reject?
```

After approval, delegate to `shopify`:
> Store ID: {store_id} (UUID). Apply the following updates: {updates_json}. For price changes: update variant price. For out-of-stock: set variant inventory quantity to 0. Return results.

### 5. Report Sync Completion
```
ℹ️ INFO — Catalog Sync Complete

Store: {store_name}
- Products checked: {total}
- Prices updated: {N}
- Quantity set to 0 (out of stock): {M}
- No changes needed: {K}
```

## Guardrails
- Never auto-publish products without owner approval.
- Never auto-unpublish without notifying owner (sync changes require approval).
- Routine stock/price sync is automated by the platform queue (`published_listings` + `pending_listing_syncs`); manual sync is exception-only.
- Margin changes are applied automatically by the system. When a sales channel margin is updated, listing prices are recalculated and pushed without agent involvement.
- **Listing price** (the price shown to customers) is calculated by the system automatically from `(purchase_price + shipping_cost) × margin` when creating/updating listings. Supervisor should not calculate or set listing prices manually. Note: `sell_price` in order line items refers to actual marketplace revenue, not the same as CJ's `sell_price` field (which is CJ's suggested retail price).
- Skip candidates that would likely exceed store `price_ceiling_usd` (estimate based on total supplier cost and store margin) or hero `target_price_range`.
- Skip products without images from supplier.
- Track supplier_sku in metafields for reliable mapping during sync.
- If supplier data is unavailable for some SKUs, report partial result and continue with available data.
