## Dropshipping order fulfillment — domain reference

In the dropshipping model a product is purchased from a supplier only after the customer
places an order on the marketplace.

**Order model (summary):** Use `order_id` (UUID) for all DB/API work. Check `has_unresolved_items`, `estimated_cost`, line items' `product_id` / prices. Statuses: `new` → `pending_approval` → `approved` → `purchasing` → `purchased` / `awaiting_payment` / `failed` → `shipped` → `fulfilled`. Supplier fields: `supplier_order_id`, `supplier_cost`, `supplier_pay_url`, tracking. Full state machine and field tables: skill **`domain-reference`**.

### Readiness checks (before starting purchase)

An order is **not ready** for supplier purchase if either condition is true:
- `has_unresolved_items == true` — line items lack product/supplier mapping.
- `estimated_cost == null` — pricing data is incomplete.

Action: notify the owner with details and do not proceed with purchase.

### Supplier purchase outcomes

| Outcome | Order status after | What happens next |
|---|---|---|
| Paid from balance | `purchased` | Await tracking (system polls automatically) |
| Payment required | `awaiting_payment` | `supplier_pay_url` is set; send to owner, wait for confirmation |
| Cost exceeded `estimated_cost` | stays `purchasing` | Notify owner with actual vs estimated cost; do not confirm |
| Supplier error | `failed` | Offer owner: retry / cancel / handle manually |

### Automation boundary

| Process | Owner | Agent role |
|---|---|---|
| Order sync from marketplace | System (cron) | React to push notification → start purchase flow |
| Status transitions `new` → `purchasing` | Agent | Drive the state machine via `PATCH /orders/{order_id}` |
| Supplier purchase | Agent → `supplier` subagent | Orchestrate and handle outcomes |
| Tracking polling | System (cron) | Act only if tracking is overdue or escalated |
| Fulfillment creation | System (on `shipped` event) | Handle failures only (delegate manual fulfillment to `shopify` or `ebay` by sales channel platform) |
| Owner notification | Agent | Notify on `fulfilled`, errors, or decisions needed |
