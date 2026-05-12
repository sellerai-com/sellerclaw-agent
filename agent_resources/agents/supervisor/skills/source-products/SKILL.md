---
name: source-products
description: "Source new products from connected suppliers and manage the SellerClaw catalog: find candidates at a supplier, save them as catalog items, list or fetch what's already in the catalog, edit catalog metadata (price, title, attributes), or remove items. Use when the owner says 'find me products to sell', 'add this to the catalog', 'what's in our catalog', 'update the catalog', 'remove from catalog', or any task involving the product catalog itself. For storefront listings use `store-products`."
---

# Source products

This skill covers two linked concerns:

1. **Sourcing** — ask the **`supplier`** subagent (via the **`supplier-search`** skill) to search and pick products at a connected supplier (e.g. CJ Dropshipping).
2. **Catalog work** — save sourced candidates to the SellerClaw DB (`agent-products batch-create`) and manage rows there (list / get / patch).

Without a catalog row no storefront publishing is possible — `store-products` reads catalog **`product_id`**s.

## Scope (what this skill is NOT)

- **Not for storefront / listing work.** Publish, edit, remove, inspect listings → **`store-products`**.
- **Cannot create catalog rows from scratch.** Real **`supplier_id`** / **`supplier_provider`** / **`supplier_product_id`** / **`supplier_variant_id`** must come from a real **`supplier`** subagent run. Never invent them.
- **Pricing and variation structure are fixed at create time.** `patch` cannot change supplier binding, variations, prices, or stock — recreate the product instead.

---

## Catalog entities (quick reference)

### Product

Internal catalog item — channel-neutral copy + supplier binding. Storefront-specific data (listing price, remote id, store URL, channel status) lives in listing/channel APIs.

- **`id`** — internal catalog id; passed as **`product_id`** elsewhere.
- **`supplier_id`**, **`supplier_provider`**, **`supplier_product_id`** — fixed supplier binding.
- **`status`** — **`sourced`** (saved from supplier search, not yet published) | **`active`** (already published) | **`archived`** (retired).
- **`variations`** — sellable SKUs under the product.

### Variation

- **`sku`** — seller-facing stable SKU; matches listings and order lines.
- **`supplier_variant_id`** — supplier-side variant id; needed to connect catalog, listings, fulfillment.
- **`attributes`** — option map, e.g. **`{"color": "red", "size": "M"}`**.
- **`available_quantity`** — supplier stock, integer ≥ 0.
- **`purchase_price`**, **`shipping_cost`** — supplier costs, decimal strings (drive every listing's price).
- **`images`** — variant-specific images; product images are the fallback.

Full schema (response, request, enums, edge cases): **`references/data-model.md`**.

---

## If you need to source products from a supplier (delegate to `supplier`)

**When:** "find products like X at the supplier", "pick candidates from CJ", "source for the DE market", seeding a new niche into the catalog.

You **do not** run supplier searches yourself — delegate to the **`supplier`** subagent. **All briefing patterns** (targeted search / broad search / refresh) and channel choice (chat vs task system) live in the **`supplier-search`** skill — use it for the delegation step.

**Required output the subagent must return** (so this skill can `batch-create` without follow-ups) — state it explicitly in the brief:

- per product: **`supplier_id`**, **`supplier_provider`**, **`supplier_product_id`**, **`name`**, **`description`**, **`category`**, **`images[]`**;
- per variation: **`supplier_variant_id`**, proposed **`sku`**, **`attributes`**, **`available_quantity`**, **`purchase_price`**, **`shipping_cost`**.

**On return — validate before saving:**

- Every candidate must carry real **`supplier_id`** / **`supplier_provider`** / **`supplier_product_id`** and every variation must carry **`supplier_variant_id`**. Missing or fabricated → send back to **`supplier`** with feedback (see `supplier-search` failure handling); do **not** proceed to `batch-create`.
- **`purchase_price`** / **`shipping_cost`** must be real numbers (not 0, not made up) — they drive every listing price.
- If the owner has not yet approved the candidate set, present it for approval **before** saving.

---

## If you need to save sourced products to the catalog

**When:** the **`supplier`** subagent returned candidates and they are ready to enter the DB.

**Command:** **`sellerclaw agent-products batch-create --json-body '{…}'`** (use **`--json-body @file`** for large payloads).

**Body:**

- **`items`** (required, array, ≥ 1) — products to create. **All-or-nothing per request** — one bad item fails the whole batch.

**Per item (`ProductCreate`):**

- **`supplier_id`** (required, UUID) — connected supplier account.
- **`supplier_provider`** (required, string) — provider code, e.g. **`cj`**.
- **`supplier_product_id`** (required, string) — supplier-side product id.
- **`name`** (required) — canonical product name.
- **`description`** (required) — canonical description.
- **`category`** (required) — taxonomy path.
- **`images`** (optional, `string[]`) — strongly recommended; downstream listings reject products without images.
- **`variations`** (required, array, ≥ 1) — see below.

**Per variation (`ProductVariationCreate`):**

- **`supplier_variant_id`** (required) — supplier-side variant id.
- **`sku`** (required) — internal SKU.
- **`name`** (required) — variant label (e.g. `Default`, `Red / M`).
- **`available_quantity`** (required, integer ≥ 0) — supplier stock.
- **`purchase_price`** (required, decimal string) — supplier unit cost.
- **`shipping_cost`** (required, decimal string) — supplier shipping for the target market.
- **`attributes`** (optional, `{string: string}`) — option map.
- **`images`** (optional, `string[]`) — variant-specific images.

**Example:**

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

**On error** — report the exact field paths the CLI rejected, fix, resend. Never silently drop items.

---

## If you need to list catalog products

**Command:** **`sellerclaw agent-products list [--status …] [--supplier-provider …]`**

**CLI options (only these are server-side filters):**

- **`--status`** (optional) — **`sourced`** | **`active`** | **`archived`**.
- **`--supplier-provider`** (optional) — provider code, e.g. **`cj`**.

Stdout wraps the payload in **`data`**; further filtering — via **`jq`**.

---

## If you need to fetch one catalog product

**Command:** **`sellerclaw agent-products get <product_id>`**

Returns the full **`Product`** with **`variations[]`** (see *Catalog entities* above).

---

## If you need to update catalog metadata

**When:** owner wants to edit description / images / category / status on an existing catalog row.

**Command:** **`sellerclaw agent-products patch <product_id> --json-body '{…}'`**

**Body — only these fields are editable (omit a field to leave it unchanged):**

- **`name`** — canonical name.
- **`description`** — canonical description.
- **`category`** — taxonomy path (or `null` to clear).
- **`images`** (`string[] | null`) — **full replacement** of the image list.
- **`status`** — **`sourced`** | **`active`** | **`archived`** (lower-case, case-sensitive).

**Cannot be patched here** (tell the owner instead of trying):

- **`supplier_id`** / **`supplier_provider`** / **`supplier_product_id`** — supplier binding is fixed at create.
- **`variations`** and any field inside (**`sku`**, **`attributes`**, **`available_quantity`**, **`purchase_price`**, **`shipping_cost`**). To change variation structure or pricing — recreate the product via *save sourced products* above.

---

## Guardrails

- Never invent **`supplier_*`** ids — they come from a real **`supplier`** subagent run via **`supplier-search`**.
- Never send **`purchase_price`** / **`shipping_cost`** as **`0`** or made-up values.
- **`batch-create`** is all-or-nothing — validate every required field before sending.
- Use **`sellerclaw describe <operation_id>`** only when (a) a CLI validation error is unclear, or (b) you need a field not listed in this SKILL or in `references/data-model.md`.

## Failure handling

- **Validation error** on **`batch-create`** / **`patch`** → report exact field paths the CLI rejected; fix and resend.
- **Supplier binding unknown / incomplete** → STOP and run **`supplier-search`** first; do not call **`batch-create`** with placeholder ids.

---

## Reference

**Full schema** (response, request bodies, enums, common pitfalls, catalog ↔ store ↔ order relationships): **`references/data-model.md`**.

**OpenAPI** (definitive JSON Schema): **`sellerclaw describe batch_create_products_products_post`**, **`sellerclaw describe patch_product_products__product_id__patch`**, … — discover via **`sellerclaw list-operations`**.
