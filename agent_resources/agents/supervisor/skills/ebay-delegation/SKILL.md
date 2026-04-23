---
name: ebay-delegation
description: "Delegate eBay store operations to the eBay Store Manager subagent."
---

# eBay Store Manager delegation

## Purpose

The eBay Store Manager (`ebay`) manages connected **eBay** stores: listings, orders,
fulfillment, locations, and seller performance. It uses **`ebay-api`** and cross-platform **`store-api`**.

## Communication contract

**Task format:** provide the target **`store_id`** (sales channel UUID) and a clear goal.
eBay-specific paths use `/ebay/stores/{store_id}/...` after `{{api_base_url}}` (no extra `/agent`).

**Response format:** structured result with outcome, data, and errors.
May return a `download_url` for file deliveries.

### Identifier routing table

| Operation | Identifier | Path pattern | Notes |
|---|---|---|---|
| Order sync (into internal DB) | `store_id` (UUID) | `POST /stores/{store_id}/orders/sync` | Cross-platform, via `store-api` |
| All eBay-specific operations | `store_id` (UUID) | `/ebay/stores/{store_id}/...` | Listings, orders, fulfillment, locations |
| Products (cross-platform) | — | `GET /products`, `POST /products` | No store identifier needed |
| Orders (cross-platform) | — | `GET /orders`, `PATCH /orders/{id}` | No store identifier needed |

## Task templates

**Sync orders:**
> Store ID: {store_id} (UUID). Sync into internal DB with `POST /stores/{store_id}/orders/sync` (see **store-api**). Return new_order_ids and updated_count.

**Create fulfillment:**
> eBay store: {store_id} (UUID). Create fulfillment for order {remote_order_id}. Tracking: {tracking_number}, carrier: {carrier}.

**Cancel an order:**
> eBay store: {store_id} (UUID). Cancel order {remote_order_id} with reason {reason} per **ebay-api**.

**Publish / revise listings:**
> eBay store: {store_id} (UUID). {listing_task_description}.

**List listings:**
> eBay store: {store_id} (UUID). List listings with filters: {filters}. Limit: {limit}.

**Locations / seller performance:**
> eBay store: {store_id} (UUID). {task_description} per **ebay-api**.

**Cross-store (eBay only):**
> For all connected **eBay** stores: {task}. Return results per store.

## Constraints

- The subagent does not communicate with the user directly.
- The subagent cannot create sessions, send messages, or manage cron jobs.
- All platform API calls go through the system API, not directly to eBay.
- For monitoring delegated task progress, use the `delegation-monitoring` skill.
