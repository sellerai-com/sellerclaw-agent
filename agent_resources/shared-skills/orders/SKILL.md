---
name: orders
description: "Read and update SellerClaw orders: list with filters, fetch one by `order_id`, change status / supplier / tracking on an order, or pull fresh orders from a storefront. Use when the owner says 'show today's orders', 'are there new orders', 'sync orders', 'refresh orders from Shopify', 'mark this shipped', 'set tracking', 'which orders are stuck', 'what's in the purchase queue', 'how much did this order cost', or any task about orders, status, tracking, costs, or unresolved line items."
---

# Orders

In SellerClaw, an **order** is one row for a single customer checkout — which store, what was sold, the internal purchase **status**, and supplier details and tracking. New orders from the user’s connected sales channels (stores) are ingested automatically.

**Important fields**


| Field                                   | Notes                                                                                                                                                                                                    |
| --------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `id`                                    | Internal order ID (usually mentioned as `order_id`).                                                                                                                                                     |
| `sales_channel_id`                      | The connected storefront ID (also mentioned as `store_id`).                                                                                                                                              |
| `remote_order_id` / `remote_order_name` | Platform order id / human label (e.g. `#1042`). Use `**remote_order_id`** for marketplace-side work; **never** use the name as a key.                                                                    |
| `status`                                | Internal purchase pipeline (`new` → … → `fulfilled` or `cancelled`).                                                                                                                                     |
| `line_items`                            | Per line: `sell_price` (revenue) and, when mapped, `product_id` + `supplier_variant_id` + supplier costs. `**product_id == null`** = line not linked to your catalog; rolls into `has_unresolved_items`. |


For the full order JSON shape, `status` transitions, and which fields you may `patch`, see references/data-model.md (read on demand, not by default).

## Commands

### List

```bash
sellerclaw agent-orders list
sellerclaw agent-orders list --status new
sellerclaw agent-orders list --sales-channel-id <sales_channel_uuid>
```

### Get one

```bash
sellerclaw agent-orders get <order_id>
```

### Patch

```bash
sellerclaw agent-orders patch <order_id> --json-body '{"status": "approved", "supplier_order_id": "...", "supplier_provider": "cj"}'
```

Use `--json-body @/tmp/patch.json` for larger bodies.

## Pull orders from a storefront into the DB

After connecting a channel, new sales are ingested on a schedule; to **trigger a sync** for one channel:

```bash
sellerclaw stores sync-orders <store_id>
```

Use the **sales channel UUID** as `store_id` (from `sales-channels`).