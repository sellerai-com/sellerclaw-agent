---
name: shopify-update-products
description: Delegate to the `shopify` subagent to change how products appear or are priced on the Shopify storefront. Use when the user says "update on Shopify", "change the listing", "edit the product page", "rewrite the description", or "change the price/image/title/tags on the shop".
---

# Update products on Shopify

Delegate listing edits to the `shopify` subagent. Your job: confirm the store, confirm the products exist, disambiguate destructive intent, hand off, report in plain language.

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
3. If the owner's intent is destructively ambiguous (e.g. "cheaper" without a number, "nicer images" without references), ask one clarifying question before delegating. Do not guess.

## Delegation

> **Update Shopify listings.**
>
> - `store_id`: `<store_id>`
> - `product_ids`: `<id_1, ...>`
> - Change intent (verbatim from the owner): `<...>`
> - Hard constraints (must not change): `<...>`

## Output to the owner

Qualitative. Use product names, not ids. Describe changes in words the owner used, not Shopify field names.

```
Updated 3 of 3 listings on <store name>:

  ✓ "Retractable Dog Leash" — price $19.90 → $17.90, title shortened for mobile.
  ✓ "Nylon Dog Collar" — main image replaced, added tag "gift".
  ✓ "Pet Harness Set" — description rewritten, tone matches premium angle.
```

## Guardrails

- Never translate ambiguous intent into a destructive change (price drop, image replacement, status flip) without owner confirmation.
- Never touch fields the owner explicitly constrained.
- Never prescribe field paths or payload shape to the subagent.

## Failure handling

- Listing not found for a product → report by product name, skip it, continue.
- Subagent flags ambiguity for a product → pass back to the owner in their own phrasing.
- Partial success → report which listings changed and which did not, one plain-language line per failure.

