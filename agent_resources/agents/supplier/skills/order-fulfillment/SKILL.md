---
name: order-fulfillment
description: "Run supplier-side fulfillment for a SellerClaw order: place the purchase at the right provider, handle payment outcome, pull tracking, and report account balance. Use when the supervisor brief references a SellerClaw `order_id` (purchase or tracking) or asks for a supplier balance check. For supplier catalog lookups use `product-search`."
---

# Supplier order fulfillment

Order-side operations at a connected supplier — purchase a SellerClaw order, handle payment outcome, pull tracking, check balance.

For catalog lookups (search / product details / shipping quote) see `product-search`.

## Common rules

- All SellerClaw DB operations key off the SellerClaw `order_id` (UUID), not `remote_order_id`.
- The provider for each line is `supplier_provider` on the SellerClaw order line — never assume a provider; pick the one each line points to. All lines of one purchase **must share the same provider**.
- Stock is checked **per variant** (`supplier_variant_id`) before purchase.
- `cost_exceeded` → never auto-confirm; report final cost vs `estimated_cost` and stop.
- `awaiting_payment` → return the payment URL to the caller; do not silently retry.
- Provider not connected → return a blocker.

---

## If you need to purchase a SellerClaw order

**Input:** SellerClaw `order_id`.

1. **Load the order** — `sellerclaw agent-orders get <order_id>`. Read `line_items[]` (per line: `supplier_provider`, `supplier_variant_id`, qty), `shipping_address`, `estimated_cost` (treat as `max_cost`), `has_unresolved_items`.
2. **Validate** — if `has_unresolved_items == true` or any line lacks `supplier_provider` / `supplier_variant_id` → return a blocker and STOP. All lines must share the same `supplier_provider`; pick that as `<provider>` for the rest of the flow.
3. **Stock check** — `sellerclaw suppliers check-stock <provider> <supplier_variant_id>` for every line; on any unavailable variant return a blocker (fail fast).
4. **Balance check** — `sellerclaw suppliers get-balance <provider>`.
5. **Create the order at the supplier** — `sellerclaw suppliers create-order <provider> --json-body '<JSON>'`. Body carries `items[]` (each with `variant_id`, `quantity`, `shipping_method`), the order's `shipping_address`, and any provider-specific payment field.
  For CJ (`cj`):
  - Shipping methods commonly used: `CJPacket` (preferred for US), `ePacket`, `USPS`.
  - Pick `pay_type` from the balance check above:
    - `pay_type = 2` (balance payment, default) when balance ≥ `estimated_cost` → on success outcome `paid`.
    - `pay_type = 1` (page payment) when balance < `estimated_cost` → response carries `pay_url`; outcome `awaiting_payment`.
    - `pay_type = 3` (create only, no payment) — only when the caller explicitly defers payment.
  - Example body:
    ```json
    {
      "items": [
        {"variant_id": "<supplier_variant_id>", "quantity": 1, "shipping_method": "CJPacket"}
      ],
      "shipping_address": {
        "country_code": "US", "province": "CA", "city": "Los Angeles", "zip_code": "90001",
        "address_line": "1 Test St", "full_name": "Jane Doe", "phone": "+13105550123"
      },
      "pay_type": 2
    }
    ```
6. **Outcome resolution:**
  - Successful balance payment → outcome `paid`.
  - Page / hosted-page payment → outcome `awaiting_payment`; the response carries the supplier-issued payment URL (CJ: `pay_url`). Do not retry — the owner pays manually. After payment, verify status with `sellerclaw suppliers get-order <provider> <supplier_order_id>`.
  - Final supplier cost > `estimated_cost` → outcome `cost_exceeded` (do **not** confirm).
  - API error → outcome `failed`.
7. **Persist** — `sellerclaw agent-orders patch <order_id> --json-body '{"supplier_order_id": "…", "supplier_provider": "<provider>", "supplier_cost": "…", "status": "…"}'` matching the outcome.
8. **Return** — outcome enum, `supplier_order_id` (supplier-side), final cost, payment URL (if any), error reason (if failed).

## If you need to pull tracking for a purchased order

**Input:** SellerClaw `order_id`.

1. `sellerclaw agent-orders get <order_id>` → read `supplier_order_id` + `supplier_provider` (use as `<provider>`).
2. `sellerclaw suppliers get-tracking <provider> <supplier_order_id>` → response: `tracking_number`, `carrier`, `events[]`.
3. Tracking found → `sellerclaw agent-orders patch <order_id> --json-body '{"tracking_number": "…", "carrier": "…"}'`; return success.
4. No tracking yet → return tracking-pending; do not retry — system polling handles routine cases. For CJ (`cj`), tracking is often not available for some time after payment, so an empty response shortly after purchase is expected.

## If you need to confirm or pay a supplier order (fallback)

- Confirm — `sellerclaw suppliers confirm-order <provider> <supplier_order_id>`.
- Pay — `sellerclaw suppliers pay-order <provider> <supplier_order_id>`. Prefer paying inside `create-order` over a separate pay step when the provider supports it (CJ does — see `pay_type` above).

## If you need to check the supplier account balance

**Command:** `sellerclaw suppliers get-balance <provider>` → response: `amount` + `currency` (decimal strings).

