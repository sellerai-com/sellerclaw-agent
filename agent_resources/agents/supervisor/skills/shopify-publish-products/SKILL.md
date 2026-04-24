---
name: shopify-publish-products
description: Delegate to the `shopify` subagent to put products on sale in the Shopify store. Use when the user says "publish to Shopify", "list on the shop", "put on sale", "push live", or wants products to appear on the storefront.
---

# Publish products to Shopify

Delegate publication to the `shopify` subagent. Your job: confirm the target store, make sure every product the owner named exists in the catalog (create if missing), hand off a single task with `store_id` + `product_ids`, then report the outcome to the owner in plain language.

## Preconditions

1. Confirm the target Shopify store:
  ```bash
   sellerclaw agent-sales-channels get <store_id>
  ```
   Require `platform == "shopify"` and an active `status`. If not, STOP and tell the owner which store step is missing.
2. Ensure every product the owner named exists in the DB:
  ```bash
   sellerclaw agent-products get <product_id>
  ```
   A missing id is **not** an error — it is a signal to create the product first via the `products` skill (batch-create), then proceed. Only escalate to the owner if `products` cannot resolve (e.g. no supplier binding available).

## Delegation

Task for the `shopify` subagent (no how-to — the subagent owns the process via its own skill):

> **Publish products to Shopify.**
>
> - `store_id`: `<store_id>`
> - `product_ids`: `<id_1, id_2, ...>`
> - Owner notes (optional, verbatim): `<...>`

## Output to the owner

Qualitative summary. Show **product names** (from `sellerclaw agent-products get`) and storefront URLs, not UUIDs. Keep it to what a non-technical person wants to see.

```
Published 4 of 5 products to <store name>:

  ✓ "Retractable Dog Leash" — https://<shop>/products/retractable-dog-leash
  ✓ "Nylon Dog Collar" — https://<shop>/products/nylon-dog-collar
  ✓ "Pet Harness Set" — https://<shop>/products/pet-harness-set
  ✓ "Chew Toy Bundle" — https://<shop>/products/chew-toy-bundle
  ✗ "Cooling Vest" — not published: main image missing.
```

## Guardrails

- Never send the delegation without a verified active Shopify channel.

## Failure handling

- Channel missing / inactive → STOP, tell the owner which store needs reconnecting.
- Product creation blocked (no supplier match, missing required field) → report by product **name** or the owner's original phrasing, explain what is missing in plain language.
- Subagent returns partial success → report which products went live and which did not, with a one-line plain-language reason per failure.

