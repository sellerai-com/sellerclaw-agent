---
name: store-update-products
description: "Delegate to the store management subagent (shopify, ebay, ...) to change how products appear or are priced on the target storefront. Use when the user says 'update on store', 'change the listing', 'edit the product page', 'rewrite the description', 'change the price/image/title/tags on the shop', or 'update on Shopify / eBay'."
---

# Update products on a storefront

If the owner's intent is destructively ambiguous (e.g. "cheaper" without a number, "nicer images" without references), ask one clarifying question before delegating. Do not guess.

## Delegation mode — chat vs tasks

Pick by the shape of the job:

- **Chat (direct message to the subagent)** — a handful of products (~≤10) with a simple, single-axis change (one field, one value), owner waiting in-session.
- **Tasks (async, trackable)** — bulky or nontrivial: large batch, mixed fields, re-pricing passes, rewrite of many descriptions, anything the owner will want progress on. Create a team task framing the whole update job + at least one agent task addressed to the picked store subagent. Use the `tasks` skill.

If in doubt, lean toward tasks.

## Delegation

Pick the subagent by the target channel's `platform` (already known from the channel lookup):

- `platform == "shopify"` → `shopify` subagent
- `platform == "ebay"` → `ebay` subagent
- additional store platforms (`amazon`, ...) → the corresponding subagent when it exists; if none exists for the platform, STOP and tell the owner the platform is not supported

Hand the work off to that subagent — either as a chat message (for the chat mode above) or by creating an agent task assigned to it (for the tasks mode above).

> **Update listings on the storefront.**
>
> - `store_id`: `<store_id>`
> - `product_ids`: `<id_1, ...>`
> - Change intent (verbatim from the owner): `<...>`
> - Hard constraints (must not change): `<...>`

## Output to the owner

Qualitative. Product names, not ids. Describe changes in the owner's words, not in platform field names.

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

