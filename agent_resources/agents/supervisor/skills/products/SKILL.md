---
name: products
description: "Work with the internal product catalog (SellerClaw database, not storefronts): create, list, fetch, edit, archive, or delete catalog products. Use when the user asks about products in general, the catalog, or saving products from a supplier — distinct from changes on a specific storefront."
---

# Data models

Only fields that are easy to misuse are listed here. Read `references/data-model.md` only when you need the full schema, request body, enums, or relationship details.

## Product

Internal catalog item saved in SellerClaw, not a storefront listing. It contains channel-neutral copy/images and supplier binding. Storefront-specific data, listing price, remote listing id, store URL, and channel status live in listing/channel APIs.

Important fields:

- `id` — internal catalog id; usually mentioned as `product_id` in skills.
- `supplier_id`, `supplier_provider`, `supplier_product_id` — fixed supplier binding. Resolve from a real supplier/integration lookup; do not invent.
- `status` — `sourced`, `active`, or `archived`. `active` products are those that are already published.
- `variations` — sellable SKUs under the product.

## Variation

One sellable variant under a product.

Important fields:

- `sku` — seller-facing stable SKU; matches listings and order lines.
- `supplier_variant_id` — supplier-side variant id; needed to connect catalog, listings, and order fulfillment.
- `attributes` — option map such as `{"color": "red", "size": "M"}`.
- `available_quantity` — supplier stock, integer `>= 0`.
- `purchase_price`, `shipping_cost` — supplier costs.
- `images` — optional variant-specific images; product images are the fallback.


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
- Run `sellerclaw describe <operation_id>` only if (a) the CLI returns a validation error you don't understand, or (b) you need a field not listed in this body or in references/data-model.md.

## Failure handling

- Validation error from `batch-create` or `patch` → report the exact field paths the CLI rejected; fix and resend. Do not silently drop items.
- Supplier binding unknown → STOP; run a `supplier` search first to obtain real supplier ids.

