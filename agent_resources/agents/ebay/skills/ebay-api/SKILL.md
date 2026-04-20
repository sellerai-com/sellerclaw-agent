---
name: ebay-api
description: Work with SellerClaw eBay Agent API endpoints for accounts, listings, orders, fulfillments, and locations.
---

# eBay API Skill

## Goal
Reference guide for `sellerclaw-api` endpoints used to interact with eBay stores.

## Platform: eBay
Use this skill for stores with `platform=ebay` in the stores table.
Path suffix after `{{api_base_url}}` (which already ends with `/agent`): `/ebay/stores/{store_id}/...`. In curl examples, use `{{api_base_url}}/ebay/...` — do **not** insert an extra `/agent/` segment.

## Base URL and Authentication
- Base URL: `{{api_base_url}}`
- Auth header: `Authorization: Bearer $AGENT_API_KEY`
- Do not print token values in logs or messages.

## Conventions
- Use `exec curl` for HTTP requests.
- All request/response bodies are JSON.
- Use `store_id` (UUID from `GET /sales-channels`) in path — not the eBay marketplace ID.
- Retry a failed endpoint at most twice, then return a blocker.

## Schemas (high-level)

`EbayAccountSchema`
| Field | Type | Notes |
|---|---|---|
| store_id | string (UUID) | SellerClaw internal ID |
| marketplace_id | string | e.g. `EBAY_US`, `EBAY_UK` |
| seller_username | string | eBay seller name |
| status | string | `active`, `inactive`, `credentials_invalid` |

`EbayListingSchema`
| Field | Type | Notes |
|---|---|---|
| id | string | Internal listing ID |
| remote_id | string | eBay item ID |
| title | string | Listing title |
| price | float | Current listing price |
| currency | string | e.g. `USD` |
| quantity | int | Available quantity |
| status | string | `active`, `ended`, `out_of_stock` |
| sku | string\|null | SKU for variant mapping |
| image_urls | string[] | Listing images |

`EbayOrderSchema`
| Field | Type | Notes |
|---|---|---|
| id | string | Internal order ID |
| remote_order_id | string | eBay order ID |
| status | string | `new`, `paid`, `shipped`, `completed`, `cancelled` |
| buyer_username | string | eBay buyer |
| total | float | Order total |
| currency | string | |
| line_items | EbayLineItemSchema[] | |
| shipping_address | object | `full_name`, `address1`, `city`, `state`, `zip`, `country_code` |
| created_at | string | ISO datetime |

`EbayLineItemSchema`
| Field | Type | Notes |
|---|---|---|
| title | string | Item title |
| sku | string\|null | |
| quantity | int | |
| price | float | Per-unit price |
| remote_id | string | eBay line item ID |

`EbayFulfillmentRequestSchema`
| Field | Type | Required |
|---|---|---|
| tracking_number | string | yes |
| carrier | string | yes (e.g. `USPS`, `UPS`, `FEDEX`, `DHL`, `OTHER`) |

`EbayLocationSchema`
| Field | Type | Notes |
|---|---|---|
| id | string | Location ID |
| name | string | Location name |
| address | object | `address1`, `city`, `state`, `zip`, `country_code` |
| type | string | `WAREHOUSE`, `STORE` |

## Endpoints

### Account

#### GET /ebay/stores/{store_id}/account
Get eBay account info and connection status.

Response: `EbayAccountSchema`

### Listings

#### GET /ebay/stores/{store_id}/listings
List eBay listings.

Query params:
| Name | Type | Required | Default |
|---|---|---|---|
| status | string | no | all |
| limit | int | no | 50 |
| offset | int | no | 0 |

Response: `{ "items": EbayListingSchema[], "total": int }`

#### POST /ebay/stores/{store_id}/listings/sync-stock
Sync stock quantities from supplier data to eBay listings.

Body:
| Field | Type | Required | Notes |
|---|---|---|---|
| items | object[] | no | `[{"sku": "...", "quantity": N}]`; if omitted, syncs all |

Response: `{ "updated": int, "failed": int, "errors": [{"sku": "...", "error": "..."}] }`

### Orders

#### GET /ebay/stores/{store_id}/orders
List eBay orders.

Query params:
| Name | Type | Required | Default |
|---|---|---|---|
| status | string | no | all |
| limit | int | no | 50 |
| offset | int | no | 0 |

Response: `{ "items": EbayOrderSchema[], "total": int }`

#### POST /ebay/stores/{store_id}/orders/{order_id}/fulfillments
Create fulfillment (ship an order) on eBay.

Body: `EbayFulfillmentRequestSchema`

Response: `{ "success": bool, "fulfillment_id": string|null, "message": string }`

Example:
```bash
curl -s -X POST "{{api_base_url}}/ebay/stores/{store_id}/orders/{order_id}/fulfillments" \
  -H "Authorization: Bearer $AGENT_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"tracking_number": "9400111899223456789012", "carrier": "USPS"}'
```

### Locations

#### GET /ebay/stores/{store_id}/locations
List fulfillment locations.

Response: `{ "items": EbayLocationSchema[] }`

#### POST /ebay/stores/{store_id}/locations
Create a fulfillment location.

Body:
| Field | Type | Required |
|---|---|---|
| name | string | yes |
| address | object | yes |
| type | string | no (default: `WAREHOUSE`) |

Response: `EbayLocationSchema`

#### DELETE /ebay/stores/{store_id}/locations/{merchant_location_key}
Delete a fulfillment location. Use the location key returned by the listing API (path parameter name in OpenAPI: `merchant_location_key`).

Response: `{ "success": bool }`

## Order sync

To pull eBay orders into the **internal** DB (same as periodic sync), use **store-api**: `POST /stores/{store_id}/orders/sync` with the sales channel UUID from `GET /sales-channels` — not the eBay-prefixed paths above.

## Guardrails
- Do not call eBay directly; use `sellerclaw-api` only.
- Use `store_id` (UUID) for all eBay endpoint paths — not the eBay seller username or marketplace ID.
- Use `remote_order_id` (eBay order ID) only when needed for display or cross-referencing; all internal operations use `order_id` (UUID).
- Keep outputs structured and concise.
- Always provide structured summaries with affected IDs, success/failure counts, and unresolved errors.
- Never print secrets or API tokens.
