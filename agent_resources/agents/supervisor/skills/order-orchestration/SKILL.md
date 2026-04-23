---
name: order-orchestration
description: "Orchestrate order lifecycle across sync, purchase, tracking, fulfillment, and exception handling."
---

# Order Orchestration Skill

## Goal
Manage the full lifecycle of customer orders using persisted Order artifacts in DB: sync, supplier purchase, fulfillment, and error handling.

## Trigger
- Push notification from periodic order sync when new orders are discovered.
- Owner command to check orders / status.
- Exception handling (payment required, cost exceeded, failed purchase).

## Step-by-Step Workflow

### 1. Load New Orders From DB
- Call `GET /orders?status=new` to get newly synced orders.
- If list is empty, finish with brief info update.
- For every order:
  - Treat DB as source of truth (do not rely on session memory).
  - **Use only `order_id` (UUID) as the identifier for any further operations** (PATCH / delegation).
  - Check `has_unresolved_items` and `estimated_cost`.
    - If `has_unresolved_items == true` OR `estimated_cost == null`: notify owner and do not start purchase.

### 2. Notify Owner
Orders are processed automatically (no approve/reject). Group new orders into one message:

```
⚡ ACTION — New Orders!

Order {order_id} — {order_name} ({date}):
- {item_title} x{qty} — SKU: {sku}
- Ship to: {city}, {country_code}
- Revenue: ${total_revenue}
- Estimated cost: ${estimated_cost or "unknown"}

{repeat for each order}

Status: processing started. I will notify you if payment is required or a blocker occurs.
```

### 3. Purchase from Supplier
For each eligible order:
- Move order through internal state machine to “processing”:
  - `PATCH /orders/{order_id}` with `status=pending_approval`
  - `PATCH /orders/{order_id}` with `status=approved`
  - `PATCH /orders/{order_id}` with `status=purchasing`

Then delegate to `supplier`:

> Purchase order {order_id}. First call GET /orders/{order_id}. Use ONLY order_id as identifier. Use line_items[].supplier_variant_id and shipping_address. Use estimated_cost as max_cost. If supplier cost exceeds max_cost: return cost_exceeded and do not confirm. Update the DB order via PATCH /orders/{order_id} (supplier_order_id, supplier_provider={provider from line_items[].supplier_provider}, supplier_cost, status transitions).

After **`sessions_spawn`**, save the **`childSessionKey`**. If the purchase runs longer than **2 minutes**, use **`sessions_history(childSessionKey)`** to check supplier progress before considering a retry or a second parallel spawn.

### 4. Get Tracking
Tracking is polled automatically by the system. Skip manual tracking checks by default.
If tracking has not appeared after the expected delivery timeframe, delegate manual check
to `supplier` with `order_id` and investigate.

### 5. Create Fulfillment
Fulfillment is created automatically when tracking is received.
If you receive a push notification about auto-fulfillment failure:
- Load order from DB (`GET /orders/{order_id}`) to get `remote_order_id`, `tracking_number`, `tracking_carrier`, and `sales_channel_id`.
- Resolve the order's marketplace platform (e.g. `GET /sales-channels` and match `sales_channel_id`, or use platform field on the order if present).
- **Shopify:** delegate to `shopify` using the channel's `store_id` (sales channel UUID):

> Store ID: {store_id} (UUID). Create fulfillment for order {remote_order_id}. Tracking number: {tracking_number}, carrier: {carrier}. Confirm when done.

- **eBay:** delegate to `ebay` using the channel UUID as `store_id`:

> eBay store: {store_id} (UUID). Create fulfillment for order {remote_order_id}. Tracking: {tracking_number}, carrier: {carrier}. Confirm when done.

### 6. Notify Owner of Completion
Use push notifications from automated tasks as your trigger.

```
ℹ️ INFO — Order Fulfilled

Order {order_id} — {order_name}:
- Purchased from {supplier_provider} (order: {supplier_order_id})
- Tracking: {tracking_number} ({carrier})
- Marketplace fulfillment created ✓
```

### 7. Error Handling

**Supplier purchase failed:**
```
🚨 CRITICAL — Purchase Failed

Order {order_id} — {order_name}:
- Reason: {error_message}
- Items: {item_titles}

Options:
1. retry — try purchasing again
2. cancel — cancel the Shopify order
3. manual — I'll handle it manually
```

If owner chooses "cancel":
- Load order from DB (by `order_id`) and resolve platform from `sales_channel_id` (same as step 5).
- **Shopify:** delegate to `shopify`: cancel on marketplace for `remote_order_id` with reason "INVENTORY", refund: true, restock: true (use channel `store_id` UUID).
- **eBay:** delegate to `ebay`: cancel on marketplace for `remote_order_id` with reason "INVENTORY" per **ebay-api** (use channel `store_id` UUID).
- Update DB: `PATCH /orders/{order_id}` with `status=cancelled`.

**Auto-fulfillment failed:**
- Load order from DB.
- Delegate manual fulfillment to `shopify` or `ebay` per the order's sales channel platform (see step 5).
- If manual fulfillment also fails, escalate to owner.

**Unresolved items / SKU mismatch:**
If order contains items without `supplier_variant_id` / `product_id` mapping:
- Notify owner with details and do not attempt purchase until resolved.

## Guardrails
- Orders are processed automatically (no approve/reject) **when supplier cost is within `estimated_cost` and balance is sufficient**.
- Treat DB order as source of truth for state instead of session memory.
- Use `estimated_cost` as max supplier cost baseline. If supplier cost is higher, require owner decision.
- Max 2 retries for supplier failures, then escalate.
- Verify `has_unresolved_items == false` before purchase.
- If payment is required (`awaiting_payment`), send `pay_url` to owner and wait for confirmation before attempting further steps.

### When owner confirmation IS required (overrides automatic processing)
- Supplier cost exceeds `estimated_cost` → present actual vs estimated, ask owner to approve or cancel.
- Insufficient supplier balance → send `pay_url` to owner, wait for payment confirmation.
- `has_unresolved_items == true` → notify owner with details, do not proceed.
- `estimated_cost == null` → pricing data incomplete, notify owner.

### When automatic processing applies (no approval needed)
- All items resolved, `estimated_cost` available, supplier cost ≤ `estimated_cost`, and balance sufficient → proceed through the state machine automatically and notify owner of progress.
