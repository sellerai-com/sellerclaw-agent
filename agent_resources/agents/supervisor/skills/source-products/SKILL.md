---
name: source-products
description: "Source new products from connected suppliers OR record user-defined products, and manage the SellerClaw catalog: find candidates at a supplier, save them as catalog items (with or without supplier binding), list or fetch what's already in the catalog, edit catalog metadata (price, title, attributes), or remove items. Use when the owner says 'find me products to sell', 'add this to the catalog', 'add my own product', 'create a product without a supplier', 'what's in our catalog', 'update the catalog', 'remove from catalog', or any task involving the product catalog itself. For storefront listings use `store-products`."
---

# Source products

This skill covers two linked concerns:

1. **Sourcing** — ask the **`supplier`** subagent (via the **`supplier-search`** skill) to search and pick products at a connected supplier (e.g. CJ Dropshipping).
2. **Catalog work** — save sourced candidates **or** owner-supplied products to the SellerClaw DB (`agent-products batch-create`) and manage rows there (list / get / patch).

Without a catalog row no storefront publishing is possible — `store-products` reads catalog **`product_id`**s.

## Two creation modes

A catalog row can be created in one of two ways:

- **Supplier-bound** (dropshipping) — the row carries **`supplier_id`** / **`supplier_provider`** / **`supplier_product_id`** that came from a real **`supplier`** subagent run. Periodic stock/price sync watches the supplier and pushes updates to live listings.
- **Supplier-less** (user-defined) — the owner is selling their own goods (handmade, locally produced, white-label inventory, etc.). All three **`supplier_*`** keys are **omitted**, the product is created with whatever name / variations / prices the owner provides, and no supplier polling runs for that row. Stock and price stay exactly as written until the owner edits them.

Both modes go through the same **`agent-products batch-create`** endpoint — the only difference is whether you send the **`supplier_*`** keys.

## Scope (what this skill is NOT)

- **Not for storefront / listing work.** Publish, edit, remove, inspect listings → **`store-products`**.
- **Supplier-bound rows must use real ids.** When creating a supplier-bound row, never invent **`supplier_id`** / **`supplier_provider`** / **`supplier_product_id`** / **`supplier_variant_id`** — they come from a real **`supplier`** subagent run.
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

- **`supplier_id`** (optional, UUID) — connected supplier account. **All-or-nothing with the other two `supplier_*` keys.** Omit for supplier-less rows.
- **`supplier_provider`** (optional, string) — provider code, e.g. **`cj`**. Omit for supplier-less rows.
- **`supplier_product_id`** (optional, string) — supplier-side product id. Omit for supplier-less rows.
- **`name`** (required) — canonical product name.
- **`description`** (required) — canonical description.
- **`category`** (required) — taxonomy path.
- **`images`** (optional, `string[]`) — strongly recommended; downstream listings reject products without images.
- **`variations`** (required, array, ≥ 1) — see below.

The three **`supplier_*`** keys are validated together: send all three or none. Sending only some (e.g. only **`supplier_provider`**) fails validation with HTTP 422.

**Per variation (`ProductVariationCreate`):**

- **`supplier_variant_id`** (required) — variant key. For supplier-bound rows this is the supplier-side variant id; for supplier-less rows it is any unique internal string per product (e.g. **`var-1`**, **`var-2`**). Must be unique among the variations of the product.
- **`sku`** (required) — internal SKU.
- **`name`** (required) — variant label (e.g. `Default`, `Red / M`).
- **`available_quantity`** (required, integer ≥ 0) — on-hand stock.
- **`purchase_price`** (required, decimal string) — unit cost. For supplier-less rows: the owner's cost basis (use **`"0"`** if they don't track one).
- **`shipping_cost`** (required, decimal string) — shipping cost for the target market; **`"0"`** if not applicable.
- **`attributes`** (optional, `{string: string}`) — option map.
- **`images`** (optional, `string[]`) — variant-specific images.

**Example (supplier-bound, dropshipping):**

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

**Example (supplier-less, user-defined product):**

Omit all three **`supplier_*`** keys. Use any unique internal string for **`supplier_variant_id`** per variation.

```bash
sellerclaw agent-products batch-create --json-body '{
  "items": [
    {
      "name": "Handmade Ceramic Mug",
      "description": "300ml, fired locally, dishwasher-safe.",
      "category": "homeware",
      "images": ["https://..."],
      "variations": [
        {
          "supplier_variant_id": "var-1",
          "sku": "MUG-WHITE",
          "name": "White",
          "attributes": {"color": "white"},
          "available_quantity": 12,
          "purchase_price": "6.50",
          "shipping_cost": "0"
        },
        {
          "supplier_variant_id": "var-2",
          "sku": "MUG-BLUE",
          "name": "Blue",
          "attributes": {"color": "blue"},
          "available_quantity": 8,
          "purchase_price": "6.50",
          "shipping_cost": "0"
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

- For supplier-bound rows: never invent **`supplier_*`** ids — they come from a real **`supplier`** subagent run via **`supplier-search`**.
- For supplier-bound rows: never send **`purchase_price`** / **`shipping_cost`** as **`0`** or made-up values — they drive listing prices and are taken from the supplier quote.
- For supplier-less rows: **`purchase_price`** / **`shipping_cost`** can be **`"0"`** if the owner does not track a cost basis, but warn them that any margin-driven listing price will then equal margin only.
- **`supplier_*`** keys are all-or-nothing — sending some but not all triggers HTTP 422.
- **`batch-create`** is all-or-nothing within a single item — validate every required field before sending.
- Use **`sellerclaw describe <operation_id>`** only when (a) a CLI validation error is unclear, or (b) you need a field not listed in this SKILL or in `references/data-model.md`.

## Failure handling

- **Validation error** on **`batch-create`** / **`patch`** → report exact field paths the CLI rejected; fix and resend.
- **Supplier-bound row, but supplier binding unknown / incomplete** → STOP and run **`supplier-search`** first; do not call **`batch-create`** with placeholder ids. If the owner explicitly says "this is my own product, no supplier", switch to the supplier-less flow instead.

---

## Reference

**Full schema** (response, request bodies, enums, common pitfalls, catalog ↔ store ↔ order relationships): **`references/data-model.md`**.

**OpenAPI** (definitive JSON Schema): **`sellerclaw describe batch_create_products_products_post`**, **`sellerclaw describe patch_product_products__product_id__patch`**, … — discover via **`sellerclaw list-operations`**.
