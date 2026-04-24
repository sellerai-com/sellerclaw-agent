---
name: store-remove-products
description: "Delegate to the store management subagent (shopify, ebay, ...) to take products off sale on the target storefront. Use when the user says 'remove from store', 'unlist', 'take off sale', 'delist', 'hide from the shop', 'retire the listing', or 'remove from Shopify / eBay'."
---

# Remove products from a storefront

**Choose the mode with the owner** before delegating:

- `unpublish` (default; reversible) — hides the listing; can be brought back later.
- `delete` (destructive, non-reversible) — permanently removes the listing on the platform. Require a confirmation handshake: ask the owner to repeat the list of product names or type "yes, delete". If the reply does not match exactly, STOP.

## Delegation

Pick the subagent by the target channel's `platform` (already known from the channel lookup):

- `platform == "shopify"` → `shopify` subagent
- `platform == "ebay"` → `ebay` subagent
- additional store platforms (`amazon`, ...) → the corresponding subagent when it exists; if none exists for the platform, STOP and tell the owner the platform is not supported

Hand this task off to that subagent.

> **Take products off sale on the storefront.**
>
> - `store_id`: `<store_id>`
> - `product_ids`: `<id_1, ...>`
> - `mode`: `unpublish` | `delete`

## Output to the owner

Plain language, product names not ids. For `delete` mode, state explicitly that the removal is permanent.

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

