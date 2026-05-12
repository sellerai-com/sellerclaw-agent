---

## name: catalog
description: "Read SellerClaw catalog products and storefront listings: list/get internal products via `agent-products`, inspect live listings on a connected store via `stores list-listings` (Shopify) or `ebay-listings list` (eBay). Use to fetch product copy/images/stock/status before campaigns, sourcing decisions, listing checks, or anywhere a task needs current catalog or storefront facts. Read-only — for catalog edits use `source-products`, for listing changes use `store-products` or the platform subagent."

# Catalog (read-only)

Two layers, distinct sources:

- **Product** — internal SellerClaw catalog row. Channel-neutral copy + supplier binding. One per sourced item.
- **Listing** — live row on a connected storefront (Shopify, eBay, …). Has its own price, stock, remote id, URL.

To target a store, resolve it first via the `sales-channels` skill (need its `id` and `platform`).

## Products (catalog)

```bash
sellerclaw agent-products list [--status sourced|active|archived] [--supplier-provider <code>]
sellerclaw agent-products get <product_id>
```

Key fields: `id`, `name`, `description`, `images`, `category`, `status`, `variations[]` (each: `sku`, `attributes`, `available_quantity`, `purchase_price`, `shipping_cost`, `supplier_variant_id`).

`status`: `sourced` (saved, not published) | `active` (published) | `archived` (retired).

## Listings (live on a store)

**Shopify** — uses the storefront's `id` as `store_id`:

```bash
sellerclaw stores list-listings <store_id> [--status active|draft|archived] [--limit N]
sellerclaw stores search-listings <store_id> --type sku|title --q "<text>"
```

**eBay**:

```bash
sellerclaw ebay-listings list <store_id> [--page-size N]
```

Key fields per listing row: `remote_id`, `sku`, `title`, `price`, `currency`, `quantity`, `image_url`, `url`.

