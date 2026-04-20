---
name: shopify-delegation
description: Delegate Shopify store operations to the Shopify Store Manager subagent.
---

# Shopify Store Manager delegation

## Purpose

The Shopify Store Manager (`shopify`) manages connected **Shopify** stores: products, orders,
fulfillment, inventory, storefront content, and theme customization. It uses **`shopify-api`**,
**`shopify-storefront-setup`**, and cross-platform **`store-api`**.

## Communication contract

**Task format:** provide the target store identifier and a clear goal. Use **`store_id`** (sales channel UUID) for **all** `/stores/{store_id}/...` paths (Shopify and cross-platform). Resolve it from `GET /sales-channels` or the stores table.

**Response format:** structured result with outcome, data, and errors.
May return a `download_url` for file deliveries.

### Identifier routing table

| Operation | Identifier | Path pattern | Notes |
|---|---|---|---|
| Order sync (into internal DB) | `store_id` (UUID) | `POST /stores/{store_id}/orders/sync` | Cross-platform |
| Shopify product batch / publish | `store_id` (UUID) | `POST/PUT/DELETE /stores/{store_id}/listings`, `.../listings/publish`, etc. | See **shopify-api** |
| Catalog listing (variants) | `store_id` (UUID) | `GET /stores/{store_id}/listings` | |
| Draft listings (DB) | `store_id` (UUID) | `GET/POST /stores/{store_id}/draft-listings`, `POST .../draft-listings/publish` | |
| Orders (marketplace) | `store_id` (UUID) | `GET /stores/{store_id}/orders`, cancel, fulfillments | |
| Storefront / theme | `store_id` (UUID) | Per **shopify-api** / **shopify-storefront-setup** | |
| Products (cross-platform) | — | `GET /products`, `POST /products` | No store identifier needed |
| Orders (cross-platform) | — | `GET /orders`, `PATCH /orders/{id}` | No store identifier needed |

**Rule of thumb:** always use **`store_id` (UUID)** in `/stores/...` paths. The API resolves the Shopify shop from the channel record; never put `*.myshopify.com` in the path.

## Task templates

**Sync orders:**
> Store ID: {store_id} (UUID). Sync into internal DB with `POST /stores/{store_id}/orders/sync` (see **store-api**). Return new_order_ids and updated_count.

**Create fulfillment:**
> Store ID: {store_id} (UUID). Create fulfillment for order {remote_order_id} (system-automated; use only if auto-fulfillment failed).
> Tracking: {tracking_number}, carrier: {carrier}.

**Cancel an order:**
> Store ID: {store_id} (UUID). Cancel order {remote_order_id}.
> Reason: {reason}. Refund: {yes/no}. Restock: {yes/no}.

**Publish products:**
> Store ID: {store_id} (UUID). Publish products: {product_ids} (`POST /stores/{store_id}/listings/publish`).

**List catalog (variants):**
> Store ID: {store_id} (UUID). List with `GET /stores/{store_id}/listings` — status: {status}. Limit: {limit}.

**Sync stock and prices:**
> Store ID: {store_id} (UUID). `POST /stores/{store_id}/listings/sync-stock` with items: {items}.

**Storefront / theme:**
> Store ID: {store_id} (UUID). {task_description} (pages, collections, menus, or theme per capabilities).

**Cross-store (Shopify only):**
> For all connected **Shopify** stores: {task}. Return results per store.

## Constraints

- The subagent does not communicate with the user directly.
- The subagent cannot create sessions, send messages, or manage cron jobs.
- All platform API calls go through the system API, not directly to Shopify.
- For monitoring delegated task progress, use the `delegation-monitoring` skill.
