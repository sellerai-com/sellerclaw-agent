---
name: store-inspect-listings
description: "Delegate to the store management subagent (shopify, ebay, ...) to see how products currently look, are priced, and are stocked on the target storefront. Use when the user says 'what is on store', 'check the shop', 'how does the listing look', 'what price/stock is live', or asks to verify a publish/update landed."
---

# Inspect storefront listings

## Delegation

Pick the subagent by the target channel's `platform` (already known from the channel lookup):

- `platform == "shopify"` → `shopify` subagent
- `platform == "ebay"` → `ebay` subagent
- additional store platforms (`amazon`, ...) → the corresponding subagent when it exists; if none exists for the platform, STOP and tell the owner the platform is not supported

Hand this task off to that subagent.

> **Read storefront listings.**
>
> - `store_id`: `<store_id>`
> - Target: either `product_ids`: `<id_1, ...>` OR `query`: `<free-text filter — status, collection, stock condition, etc.>`
> - `fields` (optional): `<what the owner cares about — price, stock, images, tags, SEO, status>`. Default: core merchandising set.

## Output to the owner

Plain language. Product names and storefront URLs (or platform-native identifier if the platform has no per-product URL). Include ids only if the owner asked for them.

```
Here's what's currently live on <store name>:

  • "Retractable Dog Leash" — $19.90, 42 in stock, active.
    https://<shop>/products/retractable-dog-leash
  • "Nylon Dog Collar" — $8.50, out of stock, active.
    https://<shop>/products/nylon-dog-collar
```

## Guardrails

- Never mutate from this skill — strictly read-only.
- Never substitute DB values when a live listing field is unavailable — report the gap.
- Never silently reconcile DB-vs-store drift; only surface it in plain language.
- Never dump raw JSON at the owner unless they explicitly asked for it.

## Failure handling

- Listing not found for a given product → report by product name, continue.
- Query returns an unmanageable count → tell the owner the count and ask them to narrow; do not page through everything.

