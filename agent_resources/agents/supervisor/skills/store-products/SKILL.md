---
name: store-products
description: "Manage how products appear on a connected storefront (Shopify, eBay, …): publish a new listing, update price/title/images, remove or pause a listing, or inspect what's currently live. Use when the owner says 'put this on sale', 'list on Shopify', 'push to eBay', 'change the price', 'update the title', 'unlist this', 'take it down', 'pause this listing', 'what's live on the store', or any task about products visible to shoppers. For sourcing and catalog writes use `source-products`."
---

# Routing

Resolve the target storefront via the `sales-channels` skill. If the owner did not name a store and exactly one matches the request, use it. If several match, ask the owner. If none, STOP and tell the owner what stores are connected.
You need the store's `id` (use as `store_id`) and `platform`.

**Map `platform` to a subagent:**

- `platform == "shopify"` → `shopify` subagent
- `platform == "ebay"` → `ebay` subagent
- other platforms (`amazon`, ...) → the corresponding subagent **only if it exists** in this workspace; if no subagent exists for the platform, STOP and tell the owner the platform is not supported. Do not improvise.

**Inspect** is always a direct chat message to that subagent. **Publish / update / remove**: small, in-session jobs → chat; large or trackable → `task-management` (team task + agent task to the same subagent). If in doubt, lean toward tasks.

# Storefront product operations

## Precondition for publishing

A product can be **published to a storefront only if it already exists as a row in the SellerClaw catalog DB**. There is **no path** that creates a brand-new product directly on the storefront.

A catalog row can be created in either of two ways — both live in the **`source-products`** skill:

1. **Supplier-bound** — brief the **`supplier`** subagent (via `source-products`) for sourcing, then persist with **`agent-products batch-create`** including the **`supplier_*`** keys.
2. **Supplier-less** — the owner provides their own product copy/variations (handmade goods, white-label inventory, etc.); persist with **`agent-products batch-create`** with **`supplier_*`** keys omitted. No supplier polling will run for that row.

Either way, you publish below with the resulting catalog **`product_id`**s. If the owner names a product that has no DB row, **STOP** and route to **`source-products`** first — never improvise the chain or skip steps.

## Publish

**When:** first-time “on the shop”, push live, list on store.

```
> **Publish products to the storefront.**
> - `store_id`: `<store_id>`
> - `product_ids`: `<id_1, id_2, ...>`
> - Owner notes (optional, verbatim): `<...>`
```

**Report:** product names; storefront URLs when the platform exposes them (else a recognizable platform id). Per product ✓/✗ with a one-line reason on failure.

**Guardrails**

- If the product already has a live listing on this store, use **update**, not a second publish.
- Never prescribe payload shape, enrichment steps, or API calls to the subagent.

## Update

**When:** change price, title, images, description, tags, etc. on listings that **already** exist on the store.

**Before delegating:** if ambiguous (“cheaper” with no number, images with no references, rewrite with no tone) — one clarifying question; do not guess. Bulk-destructive changes (e.g. large price drops, many images/descriptions at once) — present a short plan and get explicit confirmation.

```
> **Update listings on the storefront.**
> - `store_id`: `<store_id>`
> - `product_ids`: `<id_1, ...>`
> - Change intent (verbatim from the owner): `<...>`
> - Hard constraints (must not change): `<...>`
```

**Report:** names; describe changes in the owner’s words, not raw field names.

**Guardrails**

- Never prescribe field paths or payload shape to the subagent.

**Failure handling**

- Listing not found for a product → report by product name, skip, continue.
- Subagent flags ambiguity for a product → pass back to the owner in their own phrasing.
- Partial success → which listings changed and which did not; one plain-language line per failure.

## Remove

```
> **Take products off sale on the storefront.**
> - `store_id`: `<store_id>`
> - `product_ids`: `<id_1, ...>`
```

**Guardrails**

- Never mutate the catalog DB product row here.

**Failure handling**

- Listing already unlisted → idempotent success; note by product name.
- Listing not found → report by product name, skip, continue.

## Inspect

**When:** what’s live, prices, stock, verify a publish/update landed.

```
> **Read storefront listings.**
> - `store_id`: `<store_id>`
> - Target: either `product_ids`: `<id_1, ...>` OR `query`: `<free-text filter>`
> - `fields` (optional): `<...>`. Default: core merchandising set.
```

**Report:** names, key facts, URLs or platform ids; include product ids only if the owner asked.

**Guardrails**

- Never substitute DB values when a live listing field is unavailable — report the gap.
- Never silently reconcile DB-vs-store drift; surface it in plain language.

**Failure handling**

- Listing not found for a product → report by product name, continue.

