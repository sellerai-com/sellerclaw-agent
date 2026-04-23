---
name: supplier-delegation
description: "Delegate supplier sourcing, purchasing, fulfillment, and tracking to the Dropshipping Supplier subagent (e.g. CJ Dropshipping)."
---
# Supplier delegation

## Purpose

The Dropshipping Supplier (`supplier`) sources products from supplier platforms,
manages purchases, and tracks fulfillment. Currently the main supported supplier
is CJ Dropshipping.

## Communication contract

**Task format:** provide a clear goal, any product/order identifiers, constraints
(price range, country, quantities), and success criteria.

**Response format:** the subagent returns a structured result with outcome, data, and
any errors. It may also return a `download_url` for file deliveries.

## Task templates

**Targeted search (user already picked the idea):**
> Search 1 best product for "{product_description}". Price range: ${min}-${max}.
> Target country: US. Return top-1 result with variants, stock, and shipping cost.
> Do NOT search broadly — one query, one result.

**Broad search (niche exploration):**
> Search products in niche "{niche}". Categories: {categories}.
> Search terms: {search_terms}. Price range: ${min}-${max}. Min candidates: {N}.
> Include stock and shipping to US in scoring.

**Purchase flow:**
> Purchase order {order_id}. First call GET /orders/{order_id} to get items, shipping
> address, and estimated_cost. Then execute supplier purchase flow and update order
> via PATCH /orders/{order_id}.

Possible purchase outcomes:
- `paid` — paid from balance, tracking pending.
- `awaiting_payment` — insufficient balance or card payment required; return `pay_url`.
- `cost_exceeded` — final cost exceeded `max_cost`; do not confirm.
- `failed` — supplier-side error.

**Get tracking:**
> Get tracking for order {order_id} (system-automated via periodic polling; use only if auto-polling missed an order). Load supplier_order_id via GET /orders/{order_id}.
> When tracking is found, save it via PATCH /orders/{order_id}.

**Refresh product data:**
> Get current prices and stock for supplier products: {product_ids} (system-automated via periodic stock/price check; use only for manual investigation).

**Check supplier balance:**
> Check current balance on supplier account.

## Constraints

- The subagent does not communicate with the user directly.
- The subagent cannot create sessions, send messages, or manage cron jobs.
- All supplier API calls go through the system API pattern: `/suppliers/{provider}/{endpoint}`.
- For monitoring delegated task progress, use the `delegation-monitoring` skill.
