---
name: store-api
description: "Platform-independent SellerClaw API endpoints for orders, products, and sales channels."
---

# Store API Skill

## Goal
Reference for sellerclaw-api endpoints that work across all store platforms.

## Base URL and Authentication
- Base URL: `{{api_base_url}}`
- Auth header: `Authorization: Bearer $AGENT_API_KEY`
- Do not print token values in logs or messages.

## Conventions
- Use `exec curl` for HTTP requests.
- All request/response bodies are JSON.
- Retry a failed endpoint at most twice, then return a blocker.

## Endpoints

### Sales channels
- `GET /sales-channels` — list connected stores (`active_only=true` by default)

### Orders (cross-platform)
- `GET /orders` — list orders (query: `status`, `sales_channel_id`)
- `GET /orders/{order_id}` — order details
- `PATCH /orders/{order_id}` — update status, supplier info, tracking
- `POST /stores/{store_id}/orders/sync` — pull unfulfilled orders from the marketplace into the local DB (`store_id` is the sales channel UUID from `GET /sales-channels`)

### Products (cross-platform)
- `GET /products` — list products (query: `status`, `supplier_provider`)
- `GET /products/{product_id}` — product details
- `POST /products` — create products in batch (see schema below)

#### POST /products — request schema

Creates one or more products linked to a supplier catalog entry.

Body:
```json
{
  "products": [
    {
      "name": "Retractable Dog Leash",
      "description": "Heavy-duty retractable leash...",
      "category": "Pet Supplies",
      "images": ["https://img.cjdropshipping.com/...jpg"],
      "supplier_product_id": "CJ_PROD_123",
      "supplier_provider": "cj",
      "variations": [
        {
          "supplier_variant_id": "CJ_VAR_456",
          "sku": "DOG-LEASH-BLK-M",
          "purchase_price": 4.20,
          "shipping_cost": 2.80,
          "available_quantity": 200,
          "attributes": {"color": "black", "size": "M"}
        }
      ]
    }
  ]
}
```

| Field | Type | Required | Notes |
|---|---|---|---|
| `products` | array | yes | Array of product objects |
| `products[].name` | string | yes | Product title |
| `products[].description` | string | no | Product description |
| `products[].category` | string | no | Product category |
| `products[].images` | string[] | yes | Image URLs from supplier |
| `products[].supplier_product_id` | string | yes | ID on the supplier platform |
| `products[].supplier_provider` | string | yes | Provider key (e.g. `cj`) |
| `products[].variations` | array | yes | At least one variation |
| `variations[].supplier_variant_id` | string | yes | Supplier variant ID |
| `variations[].sku` | string | yes | Cross-system SKU |
| `variations[].purchase_price` | decimal | yes | Supplier cost per unit |
| `variations[].shipping_cost` | decimal | yes | Supplier shipping per unit |
| `variations[].available_quantity` | int | yes | Current supplier stock |
| `variations[].attributes` | object | no | e.g. `{"color": "black", "size": "M"}` |

Response: `{ "products": [{ "id": "uuid", "name": "...", "status": "sourced", ... }] }`

Products are created with status `sourced`. To list them on a marketplace, use the platform-specific listing creation endpoints.

### Listing sync (cross-platform)
- `POST /listing-sync/published` — register marketplace variant mappings after publishing via the eBay agent flow (or similar). Payload: `product_id`, `sales_channel_id`, `variants[{supplier_variant_id, sku, remote_id}]`. See **catalog-management** skill and `listing-sync` module for full schema.

### Files
File upload: see **file-storage** skill (text: `POST /files/`, binary: `POST /files/upload`, max 10 MB, TTL 7d).

## Store-specific endpoints
Use the `Platform` column in the stores table to determine which platform skill to reference
for store-scoped operations (for example, `shopify-api` for Shopify stores and `ebay-api` for
eBay stores).
