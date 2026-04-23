---
name: store-reporting
description: "Build store health and performance reports from products, orders, and channel metadata."
---

# Store Reporting Skill

## Goal
Generate clear reports about connected store state on owner request.

## Workflow
1. Identify target store(s) from the stores table. If not specified, report all active stores.
2. Collect data directly from `sellerclaw-api` using `exec curl`. Cross-platform **`store-api`** endpoints (orders, products, sales channels) are available to you here without delegation.
3. For **platform-specific** store data (Shopify Admin–style details, eBay listing nuances), delegate to **`shopify`** or **`ebay`** using **`shopify-delegation`** / **`ebay-delegation`**.
4. Format response as compact tables/code blocks suitable for Telegram.
5. Add a short recommendation when anomalies are detected.

## Useful queries
- Stores:
  - `GET {{api_base_url}}/sales-channels?active_only=true`
- Products:
  - `GET {{api_base_url}}/products?status=active&limit=100`
- Orders:
  - `GET {{api_base_url}}/orders?status=any&limit=20`
- Platform-specific details (via subagents):
  - Shopify → delegate to `shopify` (uses `shopify-api`).
  - eBay → delegate to `ebay` (uses `ebay-api`).

## Report templates

### Store overview
```
ℹ️ INFO — Store Report

Store: {store_name} (ID: {store_id}, domain: {domain})
Products (active): {products_count}
Orders (open): {open_orders}
Orders (recent): {recent_orders}
```

### Products snapshot
```
📦 Products — {store_name}
 # | Title | Price | Stock
 1 | ...   | ...   | ...
```

### Orders snapshot
```
📋 Orders — {store_name}
 # | Order | Status | Date | Items
 1 | ...   | ...    | ...  | ...
```

## Guardrails
- If data is partial, report available part and clearly note the gap.
- For large collections, show top items and include total count.
- Highlight out-of-stock items and failed statuses.
