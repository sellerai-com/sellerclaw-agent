---

## name: shopify-inspect-listings
description: Delegate to the `shopify` subagent to see how products currently look, are priced, and are stocked on the Shopify storefront. Use when the user says "what's on Shopify", "check the shop", "how does the listing look", "what price/stock is live", or asks to verify a publish/update landed.

# Inspect Shopify listings

Delegate a read-only lookup. Returns listing state as it is in Shopify — which can diverge from the DB product row.

## Preconditions

1. Confirm the Shopify store:
  ```bash
   sellerclaw agent-sales-channels get <store_id>
  ```
   Require `platform == "shopify"` and active `status`.
2. If the owner referenced specific products, sanity-check them:
  ```bash
   sellerclaw agent-products get <product_id>
  ```
   Missing id → the owner may have meant something else; ask before assuming.

## Delegation

> **Read Shopify listings.**
>
> - `store_id`: `<store_id>`
> - Target: either `product_ids`: `<id_1, ...>` OR `query`: `<free-text filter — status, collection, stock condition, etc.>`
> - `fields` (optional): `<what the owner cares about — price, stock, images, tags, SEO, status>`. Default: core merchandising set.

## Output to the owner

Plain language. Use product names and URLs; include ids only if the owner asked for them.

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

