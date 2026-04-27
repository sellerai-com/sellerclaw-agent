# Sales channel model reference

## Record fields


| Field                 | Type          | Notes                                                                           |
| --------------------- | ------------- | ------------------------------------------------------------------------------- |
| `id`                  | UUID          | Primary key — reuse as `sales_channel_id` / `store_id` in **sellerclaw** paths. |
| `platform`            | string        | platform slug e.g. `shopify`, `ebay`                                            |
| `status`              | string        | `active` | `inactive` | `credentials_invalid`                                   |
| `domain`              | string | null | store domain e.g. `mystore.myshopify.com` for Shopify                           |
| `margin`              | number        | Cost multiplier for listing price (e.g. `1.15` = 15% markup).                   |
| `name`, `description` | string        | Labels for the owner.                                                           |
| `categories`          | list          | Store-level category configuration.                                             |


## Background behavior

- **When `margin` changes** — the system recalculates listing prices for that channel and pushes updates to the marketplace. Do not manually re-price listings for a margin change.
- **When supplier data changes (price or stock)** — the system processes stock/price sync to connected listings. The agent is not the driver of that loop.

