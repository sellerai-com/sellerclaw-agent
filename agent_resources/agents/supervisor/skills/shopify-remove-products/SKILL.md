---
name: shopify-remove-products
description: Delegate to the `shopify` subagent to take products off sale on the Shopify storefront. Use when the user says "remove from Shopify", "unlist", "take off sale", "delist", "hide from the shop", or "retire the listing".
---

# Remove products from Shopify

Delegate delisting to the `shopify` subagent. Only the Shopify side changes — the DB product row is **not** touched here.

## Preconditions

1. Confirm the Shopify store:
  ```bash
   sellerclaw agent-sales-channels get <store_id>
  ```
   Require `platform == "shopify"` and active `status`.
2. Confirm each product exists in the DB:
  ```bash
   sellerclaw agent-products get <product_id>
  ```
   Missing id → the owner likely referred to something that no longer exists in the catalog; check before creating anything. In most removal flows, if the product is unknown, ask the owner to clarify rather than auto-create.

## Delegation

> **Take products off sale on Shopify.**
>
> - `store_id`: `<store_id>`
> - `product_ids`: `<id_1, ...>`
> - `mode`: `unpublish` | `delete`

## Output to the owner

Plain language, names not ids:

```
Unpublished 2 of 2 listings on <store name>:

  ✓ "Retractable Dog Leash" — hidden from storefront, can be republished.
  ✓ "Cooling Vest" — deleted.
```

## Guardrails

- Default to `unpublish`. Use `delete` only on explicit owner request plus confirmation handshake.
- Never mutate the DB product row here.

## Failure handling

- Listing already unlisted → idempotent success, note it by product name.
- Listing not found → report by product name, skip, continue.
- Partial success → plain-language per-product summary; no auto-retry.

