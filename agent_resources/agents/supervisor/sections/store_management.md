## Store management — domain reference

A **sales channel** is a user's online store on a marketplace platform (Shopify, eBay)
connected to SellerClaw. The user may have multiple sales channels across different
platforms.

Store operations are handled by two subagents: **Shopify Store Manager (`shopify`)** and **eBay Store Manager (`ebay`)**. Delegate using **`shopify-delegation`** or **`ebay-delegation`** according to the channel's `platform`.

**Key fields:** Sales Channel `id` (UUID), `platform`, `domain`, `margin`. Order `id` (UUID) is the only key for PATCH/delegation. Line items: `product_id` (null = unresolved), `supplier_variant_id`, `sell_price`, `purchase_price`, `shipping_cost`, `quantity`. Order statuses flow `new` → `pending_approval` → `approved` → `purchasing` → … — full tables, transitions, Product/Variation/Published Listing schemas, and identifier matrix: read skill **`domain-reference`** before deep work on catalog or orders.

### Order (store perspective)

New orders are synced periodically by the system; when new orders appear, the supervisor
receives a push notification.

### Identifier conventions (summary)

| Identifier | Scope | When to use |
|---|---|---|
| `sales_channel_id` / `store_id` (UUID) | Internal DB | All store-scoped agent paths (`/stores/{store_id}/...`), eBay (`/ebay/stores/{store_id}/...`), `POST /stores/{store_id}/orders/sync` |
| `domain` | Platform | Shop hostname on the channel record — for display/context only; API paths use `store_id` |
| `order_id` (UUID) | Internal DB | **Only** identifier for order PATCH / delegation |
| `remote_order_id` | Marketplace | Platform API calls via `shopify` or `ebay` subagent (fulfillment, cancel) |
| `product_id` (UUID) | Internal DB | API calls to `/products/...` |
| `supplier_variant_id` | Supplier | Maps a product variation to supplier catalog |
| `sku` | Cross-system | Matches marketplace listing ↔ supplier variation |

### Automated processes

Order sync, stock/price sync, margin recalculation, and listing sync are system-automated
(see "System automations" in the core section). Use manual endpoints only for investigation
or error recovery.
