---
name: products
description: Work with the internal product catalog — create, look up, edit, archive, or remove products. Use when the user says "add a product", "save this product", "what products do we have", "show product X", "edit the product", "rename the product", "archive the product", or "remove/delete a product", or asks about the catalog outside of a specific storefront.
---

# Products

A **product** is an internal SellerClaw catalog entity (DB row) bound to a supplier variant. One product can be published as **listings** on many sales channels (Shopify, eBay, ...). The product holds channel-agnostic data (supplier link, cost, base copy, images); the listing holds channel-specific presentation.

## Data model (non-obvious fields)

The table below covers only what you would otherwise get wrong. The **full product model** (every field, create/patch request bodies, enum values, validation pitfalls) lives in `[references/data-model.md](references/data-model.md)` — read it only when you need a field that is not here, not by default.

**Product**


| Field                                                     | Notes                                                                                                                                                                                                      |
| --------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `id`                                                      | Pass as `product_id` to channel skills.                                                                                                                                                                    |
| `supplier_id`, `supplier_provider`, `supplier_product_id` | Triple that binds the product to a supplier catalog entry. Resolve `supplier_id` via `sellerclaw agent-context list-integrations`; `supplier_provider` is a code like `cj`. **Not editable after create.** |
| `status`                                                  | Enum: `sourced` → `active` → `archived`.                                                                                                                                                                   |
| `variations`                                              | One entry per SKU — see below. **Not editable via `patch`**; re-create the product if variations must change.                                                                                              |


**Variation**


| Field                             | Notes                                                                                |
| --------------------------------- | ------------------------------------------------------------------------------------ |
| `supplier_variant_id`             | Supplier-side variant id.                                                            |
| `attributes`                      | Option map, e.g. `{"color": "red", "size": "M"}`.                                    |
| `available_quantity`              | Integer ≥ 0.                                                                         |
| `purchase_price`, `shipping_cost` | Decimal **strings** (not numbers). Supplier cost and shipping for the target market. |


Listing price is **not** stored on the product — it is computed per channel as `(purchase_price + shipping_cost) × channel.margin` when the listing is created.

## Commands

### List

```bash
sellerclaw agent-products list
sellerclaw agent-products list --status active
sellerclaw agent-products list --supplier-provider cj
```

Only `--status` and `--supplier-provider` are supported server-side. Stdout wraps the payload in `data`; further filtering via `jq`.

### Get one

```bash
sellerclaw agent-products get <product_id>
```

### Batch create

Inspect the full body schema before building:

```bash
sellerclaw describe batch_create_products_products_post
```

Required per item: `supplier_id`, `supplier_provider`, `supplier_product_id`, `name`, `description`, `category`, `variations` (≥1). Required per variation: `supplier_variant_id`, `sku`, `name`, `available_quantity`, `purchase_price`, `shipping_cost`.

```bash
sellerclaw agent-products batch-create --json-body '{
  "items": [
    {
      "supplier_id": "<supplier_uuid>",
      "supplier_provider": "cj",
      "supplier_product_id": "<supplier_product_id>",
      "name": "...",
      "description": "...",
      "category": "...",
      "images": ["https://..."],
      "variations": [
        {
          "supplier_variant_id": "<supplier_variant_id>",
          "sku": "...",
          "name": "Default",
          "attributes": {},
          "available_quantity": 50,
          "purchase_price": "4.20",
          "shipping_cost": "2.80"
        }
      ]
    }
  ]
}'
```

For large bodies:

```bash
sellerclaw agent-products batch-create --json-body @/tmp/products.json
cat /tmp/products.json | sellerclaw agent-products batch-create --json-body @-
```

### Patch (metadata only)

Editable fields: `name`, `description`, `images`, `category`, `status`. Omit a field to leave it unchanged.

```bash
sellerclaw describe patch_product_products__product_id__patch
```

```bash
sellerclaw agent-products patch <product_id> --json-body '{
  "name": "...",
  "description": "...",
  "category": "...",
  "images": ["https://..."],
  "status": "active"
}'
```

If the owner wants to change pricing, stock, the supplier binding, or variation structure — `patch` cannot do that.

## Guardrails

- Never invent `supplier_id` / `supplier_product_id` / `supplier_variant_id` — resolve them from a real supplier call.
- Never send `purchase_price` / `shipping_cost` as `0` or made-up values — costs drive every listing's price.
- Never call `patch` with fields it does not accept (`supplier_`*, `variations`, prices, stock) — the call will silently ignore them at best; the owner's intent will not be applied.
- `batch-create` is all-or-nothing per request — validate required fields before sending.

## Failure handling

- Validation error from `batch-create` or `patch` → report the exact field paths the CLI rejected; fix and resend. Do not silently drop items.
- Supplier binding unknown → STOP; run a `supplier` search first to obtain real supplier ids.

