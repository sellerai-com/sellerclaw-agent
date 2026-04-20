---
name: cj-dropshipping
description: Use CJ Dropshipping through sellerclaw-api for product sourcing, shipping quotes, purchasing, and tracking.
---

# CJ Dropshipping Skill

## Goal
Work with CJ Dropshipping through `sellerclaw-api` supplier endpoints: product sourcing, stock checks, shipping quotes, order placement with `pay_type`, pay-url handling for manual payment, and tracking.

## Base URL and Authentication
- Base URL: `{{api_base_url}}`
- Auth header: `Authorization: Bearer $AGENT_API_KEY`
- Do not print token values in logs or final messages.

Notes:
- These endpoints use flexible auth (header or `?token=...`). Prefer the header.
- The CJ API key is configured server-side (CJ_API_KEY). If missing, you will get `503 CJ_API_KEY is not configured`.

## Provider
- Provider id: `cj`
- All endpoints are under: `/suppliers/{provider}/...` (use `provider=cj`)

## CJ-specific notes
- Search results are paginated: `page` + `page_size` (max `page_size=200`).
- Stock must be checked by `variant_id` (not by `product_id`).
- Shipping methods (common): `CJPacket`, `ePacket`, `USPS` (for US prefer `CJPacket`).
- Tracking may not be available immediately after payment.
- `POST /suppliers/cj/orders` supports `pay_type`:
  - `1` = page payment (returns `pay_url` for manual payment),
  - `2` = balance payment,
  - `3` = create only.

Product search/detail/stock/shipping **response field definitions** (SupplierProductSchema, variants, stock, quotes, `pay_type`, pagination): see **Supplier data schemas** in your agent core config — do not duplicate here.

## Schemas — search & orders

`ProductSearchResultSchema`: `products[]`, `total`, `page`, `page_size`.

`CreateOrderRequestSchema`: `items` (OrderItemSchema[]), `shipping_address` (AddressSchema), `pay_type` (optional, default 2).

`OrderItemSchema`: `variant_id`, `quantity` (>0), `shipping_method`.

`AddressSchema`: `country_code`, `province`, `city`, `zip_code`, `address_line`, `full_name`, `phone` (all required).

`OrderResultSchema`: `order_id`, `status`, `message`, `pay_url` (nullable).

`OrderStatusSchema`: `order_id`, `status`, `created_at`, `updated_at` (nullable ISO).

`PaymentResultSchema`: `order_id`, `success`, `message`.

`BalanceSchema`: `amount`, `currency` (decimals as strings).

`TrackingInfoSchema`: `order_id`, `tracking_number`, `carrier`, `events[]`.

`TrackingEventSchema`: `timestamp`, `description`, `location`.

## Endpoints

### GET /suppliers/cj/products
Search products. Query: `query` (required), `page`, `page_size` (default 20). Response: `ProductSearchResultSchema`.

### GET /suppliers/cj/products/{product_id}
Product details. Response: `SupplierProductSchema` (see shared supplier schemas).

### GET /suppliers/cj/products/{product_id}/variants
Response: `ProductVariantSchema[]`.

### GET /suppliers/cj/stock/{variant_id}
Response: `StockInfoSchema`.

### POST /suppliers/cj/shipping/calculate
Body: `CreateOrderRequestSchema` (`shipping_method` required on items even for quotes; e.g. `CJPacket`). Response: `ShippingQuoteSchema[]`.

Example:
```bash
curl -s -X POST "{{api_base_url}}/suppliers/cj/shipping/calculate" \
  -H "Authorization: Bearer $AGENT_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "items": [{"variant_id": "VID", "quantity": 1, "shipping_method": "CJPacket"}],
    "shipping_address": {
      "country_code": "US",
      "province": "CA",
      "city": "Los Angeles",
      "zip_code": "90001",
      "address_line": "Main street 1",
      "full_name": "John Doe",
      "phone": "+15555555555"
    }
  }'
```

### POST /suppliers/cj/orders
Body: `CreateOrderRequestSchema`. Response: `OrderResultSchema`.

Example:
```bash
curl -s -X POST "{{api_base_url}}/suppliers/cj/orders" \
  -H "Authorization: Bearer $AGENT_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "items": [{"variant_id": "VID", "quantity": 1, "shipping_method": "CJPacket"}],
    "pay_type": 2,
    "shipping_address": {
      "country_code": "US",
      "province": "CA",
      "city": "Los Angeles",
      "zip_code": "90001",
      "address_line": "Main street 1",
      "full_name": "John Doe",
      "phone": "+15555555555"
    }
  }'
```

### POST /suppliers/cj/orders/{order_id}/confirm
Response: `OrderResultSchema`.

### POST /suppliers/cj/orders/{order_id}/pay
Response: `PaymentResultSchema`. Prefer `pay_type=2` on create; use this only as fallback.

### GET /suppliers/cj/orders/{order_id}
Response: `OrderStatusSchema`.

### GET /suppliers/cj/orders/{order_id}/tracking
Response: `TrackingInfoSchema`.

### GET /suppliers/cj/balance
Response: `BalanceSchema`.

## Purchase Workflow (recommended)
1. Balance check: `GET /suppliers/cj/balance`.
2. If `balance >= estimated_cost`, call `POST /suppliers/cj/orders` with `pay_type=2`.
3. If `balance < estimated_cost`, call `POST /suppliers/cj/orders` with `pay_type=1`.
4. If response contains `pay_url`, return `awaiting_payment` + `pay_url` to supervisor.
5. After owner confirms payment, verify via `GET /suppliers/cj/orders/{order_id}`.
6. Tracking: `GET /suppliers/cj/orders/{order_id}/tracking` (may not be ready immediately).

## Guardrails
- Retry failed API calls at most twice.
- If the same endpoint fails twice, return blocker with cause.
- Do not print API tokens in responses.
- Always do stock check before creating an order (fail fast on unavailable variants).
