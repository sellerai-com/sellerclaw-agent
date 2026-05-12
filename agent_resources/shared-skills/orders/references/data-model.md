# Order model data

## Identifier conventions


| Identifier                             | Scope        | When to use                                                                                                                                  |
| -------------------------------------- | ------------ | -------------------------------------------------------------------------------------------------------------------------------------------- |
| `sales_channel_id` / `store_id` (UUID) | Internal DB  | All store-scoped CLI: `sellerclaw stores ...`, including eBay store commands and `sellerclaw stores sync-orders <store_id>`.                 |
| `domain`                               | Platform     | **Informational only** (e.g. `mystore.myshopify.com` on the channel row) — **never** use hostname as a key; use the UUID.                    |
| `order_id` (UUID)                      | Internal DB  | **Only** key for `agent-orders get` / `patch` and internal delegation keyed on the SellerClaw order row.                                     |
| `remote_order_id`                      | Marketplace  | Store cancel/fulfill and adapter calls on the **platform** — when delegating to `shopify` / `ebay` with the marketplace order id from `get`. |
| `product_id` (UUID)                    | Internal DB  | Product catalog: `agent-products` and related product flows.                                                                                 |
| `supplier_variant_id`                  | Supplier     | Maps a product variation to the supplier catalog; also on line items and listings.                                                           |
| `sku`                                  | Cross-system | Match marketplace listing to supplier **variation** / order line.                                                                            |


## Status and transitions

### `OrderStatus`


| Status             | Meaning                                          |
| ------------------ | ------------------------------------------------ |
| `new`              | Synced from marketplace; not yet processed.      |
| `pending_approval` | Queued for purchase processing.                  |
| `approved`         | Cleared for supplier purchase.                   |
| `purchasing`       | Supplier purchase in progress.                   |
| `purchased`        | Supplier confirmed; awaiting shipment.           |
| `awaiting_payment` | Supplier needs payment (e.g. pay URL).           |
| `shipped`          | Dispatched; tracking may be present.             |
| `fulfilled`        | Marketplace fulfillment recorded — **terminal**. |
| `cancelled`        | **Terminal**.                                    |
| `failed`           | Supplier purchase failed — **retryable**.        |


### Allowed `status` transitions (`patch`)


| From               | To                                        |
| ------------------ | ----------------------------------------- |
| `new`              | `pending_approval`                        |
| `pending_approval` | `approved`, `cancelled`                   |
| `approved`         | `purchasing`                              |
| `purchasing`       | `purchased`, `awaiting_payment`, `failed` |
| `awaiting_payment` | `purchased`                               |
| `purchased`        | `shipped`                                 |
| `shipped`          | `fulfilled`                               |
| `fulfilled`        | *(none — terminal)*                       |
| `cancelled`        | *(none — terminal)*                       |
| `failed`           | `approved` (retry), `cancelled`           |


Normal path: `new` → `pending_approval` → `approved` → `purchasing` → `purchased` → `shipped` → `fulfilled`.

## Order fields


| Field                             | Type     | Notes                                                                                           |
| --------------------------------- | -------- | ----------------------------------------------------------------------------------------------- |
| `id`                              | UUID     | **Only** key for `get` / `patch` / internal delegation.                                         |
| `user_id`                         | UUID     | Workspace owner.                                                                                |
| `sales_channel_id`                | UUID     | Source storefront; same as `store_id` in store-scoped CLI.                                      |
| `remote_order_id`                 | string   | Marketplace order id — subagent / cancel / fulfill.                                             |
| `remote_order_name`               | string   | Display (e.g. `#1042`) — **not** a key.                                                         |
| `status`                          | string   | Internal state — see table above.                                                               |
| `financial_status`                | string   | Payment state from the marketplace.                                                             |
| `customer_name`, `customer_email` | string   | null                                                                                            |
| `shipping_address`                | object   | `full_name`, `address1`, `address2?`, `city`, `province?`, `zip_code`, `country_code`, `phone?` |
| `line_items`                      | array    | See below.                                                                                      |
| `remote_created_at`               | datetime | From marketplace.                                                                               |
| `created_at`, `updated_at`        | datetime | SellerClaw row.                                                                                 |


## Order line item


| Field                 | Type           | Notes                             |
| --------------------- | -------------- | --------------------------------- |
| `remote_line_item_id` | string         | Platform line id.                 |
| `title`               | string         |                                   |
| `sku`                 | string         | null                              |
| `quantity`            | int            |                                   |
| `sell_price`          | decimal string | Revenue per unit (customer paid). |
| `remote_variant_id`   | string         | null                              |
| `product_id`          | UUID           | null                              |
| `supplier_variant_id` | string         | null                              |
| `supplier_provider`   | string         | null                              |
| `purchase_price`      | decimal string | null                              |
| `shipping_cost`       | decimal string | null                              |


## Computed


| Property               | Logic                                                                                                |
| ---------------------- | ---------------------------------------------------------------------------------------------------- |
| `total_revenue`        | Σ (`sell_price` × `quantity`) — decimals as **strings** in JSON.                                     |
| `estimated_cost`       | Σ (`purchase_price` + `shipping_cost`) × `quantity`; `null` if any line lacks prices or mapping. |
| `has_unresolved_items` | `true` if any line has `product_id == null`.                                                         |


## Supplier and tracking


| Field                                                 | Set when                                          |
| ----------------------------------------------------- | ------------------------------------------------- |
| `supplier_order_id`                                   | Supplier confirms purchase.                       |
| `supplier_provider`                                   | Same provider code as supplier integration calls. |
| `supplier_cost`                                       | Confirmed cost (decimal string in response).      |
| `supplier_pay_url`                                    | Status moves toward payment needed.               |
| `tracking_number`, `tracking_carrier`, `tracking_url` | Supplier ships.                                   |


`patch` can update: `status`, `supplier_order_id`, `supplier_provider`, `supplier_cost`, `tracking_`*, `supplier_pay_url` — see `sellerclaw describe patch_order_orders__order_id__patch`.

## Line item ↔ marketplace

Use `remote_line_item_id` / `remote_variant_id` when correlating to platform APIs through `shopify` / `ebay` subagents.