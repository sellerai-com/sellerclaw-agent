---
name: shopify-api
description: Work with Shopify through sellerclaw-api endpoints for products, listings, orders, fulfillment, and inventory.
---

# Shopify API Skill

## Goal
Reference guide for `sellerclaw-api` endpoints used to interact with Shopify stores.

## Platform: Shopify
Use this skill for stores with `platform=shopify` in the stores table.
Use **sales channel UUID** (`store_id`) in every store path: `/stores/{store_id}/...` (not the `.myshopify.com` domain).

Resolve `store_id` from `GET /sales-channels` (or the stores table in agent context): it is the same UUID used for cross-platform store-api calls.

## Base URL and Authentication
- Base URL: `{{api_base_url}}`
- Auth header: `Authorization: Bearer $AGENT_API_KEY`
- Do not print token values in logs or messages.

## Conventions
- Use `exec curl` for HTTP requests.
- All requests/response bodies are JSON.
- Retry a failed endpoint at most twice, then return a blocker.

## Schemas (high-level)

`ShopifyProductSchema`
| Field | Type | Notes |
|---|---|---|
| id | string | Shopify product ID |
| title | string | |
| product_type | string | Category / type |
| status | string | `active`, `draft`, `archived` |
| variants | ShopifyVariantSchema[] | |
| images | object[] | `[{"src": "https://..."}]` |
| metafields | object\|null | Custom fields (e.g. `supplier_sku`) |

`ShopifyVariantSchema`
| Field | Type | Notes |
|---|---|---|
| id | string | Shopify variant ID |
| sku | string | |
| price | string (decimal) | Listing price |
| inventory_quantity | int | Current stock |
| title | string | Variant option combo (e.g. "Black / M") |

`ShopifyOrderSchema`
| Field | Type | Notes |
|---|---|---|
| id | string | Shopify order ID |
| name | string | Display name (e.g. "#1042") |
| financial_status | string | `paid`, `pending`, `refunded`, etc. |
| fulfillment_status | string\|null | `fulfilled`, `partial`, null |
| total_price | string (decimal) | |
| line_items | ShopifyLineItemSchema[] | |
| shipping_address | object | |
| created_at | string | ISO datetime |

`ShopifyLineItemSchema`
| Field | Type | Notes |
|---|---|---|
| id | string | |
| title | string | |
| sku | string\|null | |
| quantity | int | |
| price | string (decimal) | Per-unit price |

`ShopifyFulfillmentRequestSchema`
| Field | Type | Required |
|---|---|---|
| tracking_number | string | yes |
| tracking_company | string | yes (e.g. `USPS`, `UPS`, `FedEx`, `DHL`) |

`ShopifyListingSchema` (draft listings in DB — `/draft-listings`)
| Field | Type | Notes |
|---|---|---|
| id | string | Internal listing ID |
| product_id | string (UUID) | Internal product reference |
| shopify_product_id | string\|null | Shopify product when published |
| status | string | Draft / published lifecycle |

`StoreInfoResponse` (`GET /info`)
| Field | Type | Notes |
|---|---|---|
| name | string | Shop name |
| domain | string | e.g. `mystore.myshopify.com` |
| currency | string | e.g. `USD` |
| email | string\|null | |
| platform | string | `shopify` |

## Endpoints

All paths below are relative to `{{api_base_url}}` and use UUID `store_id`.

### Catalog (Shopify products via adapter)

#### GET /stores/{store_id}/listings
List storefront catalog rows (one row per variant). Query: `status` (default `active`), `limit`.

Response: `{ "items": [...] }` with variant-oriented fields (`remote_id`, `sku`, `title`, `price`, `quantity`, …).

### Shopify Admin product batch (GraphQL-backed)

These use path **`/listings`** for historical reasons; they operate on **Shopify products** (batch create/update/delete/publish), not the internal draft-listing model.

#### POST /stores/{store_id}/listings
Batch create products. Body: `{ "items": [ ProductCreateItem, ... ] }` (see API schema `ProductBatchCreateRequest`).

#### PUT /stores/{store_id}/listings
Batch update products. Body: `{ "items": [ ... ] }` (`ProductBatchUpdateRequest`).

#### DELETE /stores/{store_id}/listings
Batch delete. Body: `{ "product_ids": [...] }`.

#### POST /stores/{store_id}/listings/sync-stock
Sync stock and prices via the **cross-platform adapter** (requires `sku` + `quantity` per item; optional `price`, `compare_at_price`, `remote_id`).

Body: `{ "items": [{ "sku": "...", "quantity": 10, "price": "19.99" }] }`.

#### POST /stores/{store_id}/listings/publish
Body: `{ "product_ids": [...], "publication_names": null }`.

#### POST /stores/{store_id}/listings/unpublish
Body: `{ "product_ids": [...] }`.

### Draft listings (internal DB → Shopify)

#### GET /stores/{store_id}/draft-listings
List draft/published listing records tied to internal products. Query: `status` (optional filter).

#### POST /stores/{store_id}/draft-listings
Create draft listing rows from internal `product_ids`. Body: `{ "product_ids": ["uuid", ...], "product_type": null }`.

#### POST /stores/{store_id}/draft-listings/publish
Publish drafts to Shopify Online Store. Body: `{ "listing_ids": ["uuid", ...] }`.

### Orders

#### GET /stores/{store_id}/orders
List marketplace orders (adapter). Query: `status` (default `unfulfilled`), `limit`.

#### POST /stores/{store_id}/orders/{order_id}/cancel
Cancel a Shopify order. Body: `{ "reason": "CUSTOMER", "refund": true, "restock": false }` (`OrderCancelRequest`).

Response: `{ "job": { ... } }` (Shopify async job payload).

#### POST /stores/{store_id}/orders/sync
Sync orders into the **internal** DB (same as other channels). No Shopify-domain path.

### Fulfillment

#### POST /stores/{store_id}/orders/{order_id}/fulfillments
Body: `{ "tracking": { "number", "company", "url?" }, "line_items": [{ "remote_line_item_id", "quantity" }] }`.

#### PUT /stores/{store_id}/fulfillments/{fulfillment_id}/tracking
Body: `{ "tracking": { "number", "company", "url?" } }`.

### Shop metadata

#### GET /stores/{store_id}/info
Unified store profile (Shopify shop fields: name, domain, currency, email, …).

### Shopify REST proxy

Forward to Shopify Admin REST under the connected store credentials:

- `GET /stores/{store_id}/proxy/{path}` (cached GETs)
- `POST`, `PUT`, `DELETE` `/stores/{store_id}/proxy/{path}`

Example: `GET .../proxy/products/count.json`

## Order sync

Use **`POST /stores/{store_id}/orders/sync`** with the sales channel UUID from `GET /sales-channels`.

## Guardrails
- Do not call Shopify directly; use `sellerclaw-api` only.
- Use **`store_id` (UUID)** for all `/stores/...` paths — never put `*.myshopify.com` in the path.
- Keep outputs structured and concise.
- Never print secrets or API tokens.
