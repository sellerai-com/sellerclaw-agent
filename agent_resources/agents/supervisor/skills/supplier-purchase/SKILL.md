---
name: supplier-purchase
description: "Delegate dropshipping fulfillment of a SellerClaw order (by `order_id`) to the `supplier` subagent — purchase, payment, tracking, and persistence back to the order; also covers supplier balance checks. Use when the task is to buy out an order at the supplier or check supplier funds."
---

# Supplier purchase (delegation)

Send the **`supplier`** subagent a SellerClaw **`order_id`**; the subagent reads the order, picks **`supplier_provider`** from its line items, runs the provider purchase flow, and persists results back to the order.

Out of scope: product info / search → **`supplier-search`**. Catalog DB writes → **`source-products`**. Storefront-side updates → **`store-products`**.

**Delivery channel:** chat message to the subagent (**`sessions_spawn`** / **`sessions_send`**).

## Briefs

### Place purchase

```
> Purchase order <order_uuid>.
> estimated_cost is max_cost. Persist supplier_order_id, supplier_provider,
> supplier_cost, status back to the order.
```

### Pull tracking (only when system polling missed it)

```
> Pull tracking for order <order_uuid>. Persist tracking_number/carrier on success.
```

### Check supplier balance

```
> Check balance for supplier provider <e.g. cj>.
```

## Outcomes the subagent reports

- **`paid`** — paid from supplier balance; tracking pending.
- **`awaiting_payment`** — manual card payment required; **`pay_url`** is included → surface it to the requester.
- **`cost_exceeded`** — final supplier cost exceeded **`estimated_cost`**; the order is **not** confirmed.
- **`failed`** — supplier-side error; the order is **not** charged.

## CJ Dropshipping payment flow (`supplier_provider = cj`)

Run by the subagent — the supervisor only consumes the outcome:

1. Balance check at CJ.
2. **Balance ≥ `estimated_cost`** → create the order with **`pay_type = 2`** (balance payment). Outcome: **`paid`**, or **`cost_exceeded`** if the final supplier cost exceeds `estimated_cost`.
3. **Balance < `estimated_cost`** → create the order with **`pay_type = 1`** (page payment). Response carries **`pay_url`**. Outcome: **`awaiting_payment`** — surface `pay_url` to the requester so the owner can pay manually.
4. After the owner pays via `pay_url`, the subagent verifies the order at CJ and persists **`supplier_order_id`** + **`supplier_cost`** + **`status`** back to the SellerClaw order.

## Failure handling

- **`cost_exceeded`** → never auto-confirm; report final cost vs `estimated_cost` and stop.
- **`awaiting_payment`** → surface **`pay_url`**; do not silently retry.
- **Order has unresolved items** (**`has_unresolved_items == true`**, or a line missing **`supplier_provider`** / **`supplier_variant_id`**) → STOP; fix via **`source-products`** / **`orders`** before retrying.
- **Subagent silent for too long** → check **`sessions_history`**; one nudge before escalating.
