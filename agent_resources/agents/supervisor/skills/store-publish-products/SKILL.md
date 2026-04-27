---
name: store-publish-products
description: "Delegate to the store management subagent (shopify, ebay, ...) to put products on sale on the target storefront. Use when the user says 'publish to ', 'list on the shop', 'put on sale', 'push live', or wants products to appear on any connected storefront."
---

# Publish products to a storefront

## Delegation mode — chat vs tasks

Pick by the shape of the job:

- **Chat (direct message to the subagent)** — a handful of products (~≤3), no enrichment heavy lifting, owner is waiting in-session.
- **Tasks (async, trackable)** — bulky or nontrivial: large batch, enrichment-heavy products, multi-step, or anything the owner will want to see progress on. Create a team task framing the whole publication job + at least one agent task addressed to the picked store subagent. Use the `task-management` skill.

If in doubt, lean toward tasks: publication is the kind of work where an audit trail and async progress are usually wanted.

## Delegation

Pick the subagent by the target channel's `platform` (already known from the channel lookup):

- `platform == "shopify"` → `shopify` subagent
- `platform == "ebay"` → `ebay` subagent
- additional store platforms (`amazon`, ...) → the corresponding subagent when it exists; if none exists for the platform, STOP and tell the owner the platform is not supported

Hand the work off to that subagent — either as a chat message (for the chat mode above) or by creating an agent task assigned to it (for the tasks mode above). No how-to — the subagent owns the process via its own platform-specific skill.

> **Publish products to the storefront.**
>
> - `store_id`: `<store_id>`
> - `product_ids`: `<id_1, id_2, ...>`
> - Owner notes (optional, verbatim): `<...>`

## Output to the owner

Qualitative summary. Show **product names** and, when the platform exposes them, storefront URLs.

```
Published 4 of 5 products to <store name>:

  ✓ "Retractable Dog Leash" — https://<shop>/products/retractable-dog-leash
  ✓ "Nylon Dog Collar" — https://<shop>/products/nylon-dog-collar
  ✓ "Pet Harness Set" — https://<shop>/products/pet-harness-set
  ✓ "Chew Toy Bundle" — https://<shop>/products/chew-toy-bundle
  ✗ "Cooling Vest" — not published: main image missing.
```

If the platform has no per-product URL, replace the URL with the platform-native identifier the owner would recognise).

## Guardrails

- Never prescribe payload shape, enrichment steps, or API calls to the subagent.

## Failure handling

- Product creation blocked (no supplier match, missing required field) → report by product **name** or the owner's original phrasing, explain what is missing in plain language.
- Subagent returns partial success → report which products went live and which did not, with a one-line plain-language reason per failure.

