---
name: domain-reference
description: "Sales Channel, Product, and Order data models — fields, statuses, state transitions, identifier conventions. Use when: interpreting order fields, status transitions, product/variation/listing structures, or routing IDs between store and supplier workflows."
---

# Domain reference (store & order)

## Sales Channel

A **sales channel** is a user's online store on a marketplace (Shopify, eBay) connected to SellerClaw.

| Field | Type | Notes |
|---|---|---|
| `id` | UUID | Primary key for all API operations |
| `platform` | `shopify` \| `ebay` | |
| `status` | `active` \| `inactive` \| `credentials_invalid` | |
| `domain` | string \| null | Store domain (required for Shopify, e.g. `mystore.myshopify.com`) |
| `margin` | float | Cost multiplier for listing price (default 1.15 = 15% markup) |
| `name`, `description` | string | Store metadata |
| `categories` | list | Store-level category configuration |

When `margin` is updated, the system automatically recalculates listing prices and pushes them to the marketplace — no agent involvement required.

When supplier data changes (price or stock), the system creates a `PendingListingSync` record and processes it automatically.

## Product data model

### Product

| Field | Type | Notes |
|---|---|---|
| `id` | UUID | Primary key |
| `supplier_id` | UUID | Supplier account reference |
| `supplier_product_id` | string | ID on the supplier platform |
| `supplier_provider` | string | Provider key (e.g. `cj`) |
| `status` | enum | See below |
| `variations` | list | At least one; each has own pricing, stock, and attributes |

**Product statuses:**

| Status | Meaning |
|---|---|
| `sourced` | Saved in DB from supplier catalog; not yet listed on any marketplace |
| `active` | Published on at least one marketplace |
| `archived` | Removed from active catalog |

### Product Variation

One sellable variant of a product:

| Field | Type | Notes |
|---|---|---|
| `supplier_variant_id` | string | Unique within product; maps to supplier catalog |
| `sku` | string | Cross-system identifier; matches marketplace listing to supplier variation |
| `purchase_price` | Decimal | Supplier cost per unit |
| `shipping_cost` | Decimal | Supplier shipping cost per unit |
| `available_quantity` | int | Current supplier stock |
| `attributes` | dict | Variant attributes (e.g. `{"color": "black", "size": "M"}`) |

### Published Listing (Product ↔ Sales Channel bridge)

Links a product variation to a marketplace listing. Used by the automated stock/price sync.

| Field | Type | Notes |
|---|---|---|
| `product_id` | UUID | → Product |
| `sales_channel_id` | UUID | → Sales Channel |
| `supplier_variant_id` | string | Which variation is listed |
| `sku` | string | Marketplace SKU |
| `remote_id` | string \| null | Marketplace listing ID (null while pending creation) |

### Entity relationships

```
Sales Channel (id, platform, domain, margin)
  ├─ Orders (sales_channel_id)
  │   └─ Line Items
  │       ├─ product_id ──→ Product (nullable: unresolved if missing)
  │       └─ supplier_variant_id ──→ Product Variation
  └─ Published Listings (sales_channel_id)
      └─ product_id ──→ Product

Product (id, supplier_provider)
  └─ Variations (supplier_variant_id, sku, pricing, stock)
```

## Order data model

An **Order** represents a customer purchase from a connected store, synced into the SellerClaw database.

### Order fields

| Field | Type | Notes |
|---|---|---|
| `id` | UUID | **The only identifier for all DB / API / delegation operations** |
| `sales_channel_id` | UUID | Which store the order came from |
| `remote_order_id` | string | Marketplace order ID (used only for platform API calls via `shopify` / `ebay` subagent) |
| `remote_order_name` | string | Human-readable (e.g. "#1042") — display only, never use as key |
| `status` | OrderStatus | Current processing state |
| `financial_status` | string | Payment status from marketplace |
| `customer_name`, `customer_email` | string \| null | Buyer info |
| `shipping_address` | object | `full_name`, `address1`, `city`, `province`, `zip_code`, `country_code`, `phone` |
| `line_items` | list | See below |

### Order Line Item

| Field | Type | Notes |
|---|---|---|
| `product_id` | UUID \| null | Link to internal Product; **null = unresolved** (not mapped) |
| `supplier_variant_id` | string \| null | Link to supplier catalog variant |
| `supplier_provider` | string \| null | Which supplier platform |
| `sell_price` | Decimal | Revenue per unit (actual price the customer paid on the marketplace) |
| `purchase_price` | Decimal \| null | Supplier cost per unit (null if unknown) |
| `shipping_cost` | Decimal \| null | Supplier shipping per unit (null if unknown) |
| `sku` | string \| null | SKU from marketplace listing |
| `quantity` | int | Units ordered |

### Order statuses

| Status | Meaning |
|---|---|
| `new` | Synced from marketplace, not yet processed |
| `pending_approval` | Queued for purchase processing |
| `approved` | Cleared for supplier purchase |
| `purchasing` | Supplier purchase in progress |
| `purchased` | Supplier confirmed the order; awaiting shipment |
| `awaiting_payment` | Supplier requires payment (insufficient balance or card payment needed) |
| `shipped` | Supplier dispatched; tracking number available |
| `fulfilled` | Marketplace fulfillment created — **terminal** |
| `cancelled` | Order cancelled — **terminal** |
| `failed` | Supplier purchase failed — **retryable** |

### State transitions

| From | Allowed targets |
|---|---|
| `new` | `pending_approval` |
| `pending_approval` | `approved`, `cancelled` |
| `approved` | `purchasing` |
| `purchasing` | `purchased`, `awaiting_payment`, `failed` |
| `awaiting_payment` | `purchased` |
| `purchased` | `shipped` |
| `shipped` | `fulfilled` |
| `fulfilled` | *(none — terminal)* |
| `cancelled` | *(none — terminal)* |
| `failed` | `approved` (retry), `cancelled` |

Normal path: `new` → `pending_approval` → `approved` → `purchasing` → `purchased` → `shipped` → `fulfilled`.

### Supplier-related order fields

These fields are `null` on a new order and populated during the purchase/shipping process:

| Field | Type | Set when |
|---|---|---|
| `supplier_order_id` | string \| null | Supplier confirms the purchase |
| `supplier_provider` | string \| null | Same value as the `/suppliers/{provider}/...` path segment |
| `supplier_cost` | Decimal \| null | Supplier confirms the purchase (actual cost) |
| `supplier_pay_url` | string \| null | Status becomes `awaiting_payment` |
| `tracking_number` | string \| null | Supplier ships the order |
| `tracking_carrier` | string \| null | Supplier ships the order |
| `tracking_url` | string \| null | Supplier ships the order |

### Computed properties (read-only, returned by API)

| Property | Type | Logic |
|---|---|---|
| `total_revenue` | Decimal | `Σ (sell_price × quantity)` across all line items |
| `estimated_cost` | Decimal \| null | `Σ (purchase_price + shipping_cost) × quantity`; `null` if any item is unresolved or missing prices |
| `has_unresolved_items` | bool | `true` if any line item has `product_id == null` (not linked to an internal product) |

## Identifier conventions

| Identifier | Scope | When to use |
|---|---|---|
| `sales_channel_id` / `store_id` (UUID) | Internal DB | **All** store-scoped agent API calls (`/stores/{store_id}/...` for Shopify, sync, listings, orders, storefront), eBay (`/ebay/stores/{store_id}/...`), order sync (`POST /stores/{store_id}/orders/sync`) |
| `domain` | Platform | **Informational only** (e.g. `mystore.myshopify.com` on the sales channel row) — **do not** use it in HTTP paths; use `store_id` |
| `order_id` (UUID) | Internal DB | **Only** identifier for order PATCH / delegation |
| `remote_order_id` | Marketplace | Platform API calls via `shopify` or `ebay` subagent (fulfillment, cancel) |
| `product_id` (UUID) | Internal DB | API calls to `/products/...` |
| `supplier_variant_id` | Supplier | Maps a product variation to supplier catalog |
| `sku` | Cross-system | Matches marketplace listing ↔ supplier variation |
