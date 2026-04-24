# Product data model — full reference

Generated from the `sellerclaw-cli` OpenAPI spec. This is an exhaustive reference; the skill body (`SKILL.md`) only lists non-obvious fields. **Read this file only when you actually need a field not covered there** — full schemas are expensive in context.

At runtime you can also pull the live schema for any operation with `sellerclaw describe <operation_id>`.

---

## Product (response shape returned by `list` / `get`)

Source: `ProductResponse`. Every field below is always present on a product (all required).

| Field | Type | Notes |
|---|---|---|
| `id` | uuid | Primary identifier. Pass as `product_id` everywhere. |
| `user_id` | uuid | Owning workspace user. |
| `supplier_id` | uuid | Connected supplier account. Resolve via `sellerclaw agent-context list-integrations`. |
| `supplier_provider` | string | Provider code, e.g. `cj`. |
| `supplier_product_id` | string | Supplier-side product id. |
| `name` | string | Canonical product name. |
| `description` | string | Canonical description. |
| `images` | string[] | Image URLs (first = hero). |
| `category` | string | Taxonomy path. |
| `status` | `ProductStatus` (string) | See enum below. |
| `variations` | `ProductVariationResponse[]` | One entry per SKU. |
| `created_at` | datetime (ISO 8601) | |
| `updated_at` | datetime (ISO 8601) | Bumped on any server-side change. |

### `ProductStatus` enum

| Value | Meaning |
|---|---|
| `sourced` | Freshly saved from a supplier search, not yet promoted to active catalog. |
| `active` | Part of the live catalog; eligible for channel listings. |
| `archived` | Retired; do not publish, do not send to supplier. |

### `ProductVariationResponse`

| Field | Type | Notes |
|---|---|---|
| `supplier_variant_id` | string | Supplier-side variant id. |
| `sku` | string | Internal SKU. |
| `name` | string | Variant label. |
| `images` | string[] | Variant-specific images (may be empty). |
| `attributes` | `{string: string}` | Option map, e.g. `{"color": "red", "size": "M"}`. |
| `available_quantity` | integer ≥ 0 | Supplier stock. |
| `purchase_price` | decimal **string** | Supplier unit cost. Format: signed decimal string; not a number. |
| `shipping_cost` | decimal **string** | Supplier shipping for the target market. Same format. |

---

## Create request — `batch-create`

Body: `ProductBatchCreateRequest` = `{"items": ProductCreate[]}`. One call per batch; the server applies all-or-nothing semantics.

### `ProductCreate` (one per item)

| Field | Required | Type | Notes |
|---|---|---|---|
| `supplier_id` | ✓ | uuid | |
| `supplier_provider` | ✓ | string | |
| `supplier_product_id` | ✓ | string | |
| `name` | ✓ | string | |
| `description` | ✓ | string | |
| `category` | ✓ | string | |
| `images` | ✗ | string[] | Optional but strongly recommended; listings without an image get rejected downstream. |
| `variations` | ✓ | `ProductVariationCreate[]` | Must have ≥1. |

### `ProductVariationCreate`

| Field | Required | Type | Notes |
|---|---|---|---|
| `supplier_variant_id` | ✓ | string | |
| `sku` | ✓ | string | |
| `name` | ✓ | string | |
| `images` | ✗ | string[] | |
| `attributes` | ✗ | `{string: string}` | |
| `available_quantity` | ✓ | integer ≥ 0 | |
| `purchase_price` | ✓ | number \| decimal string | Server normalises to decimal string. |
| `shipping_cost` | ✓ | number \| decimal string | Server normalises to decimal string. |

Fields NOT accepted on create (do not put them in the body, the server will 422):
- `id`, `user_id`, `status`, `created_at`, `updated_at` — server-assigned.

---

## Patch request — `patch`

Body: `ProductPatchRequest`. "Partial update for user-editable product metadata. Omit a field to leave it unchanged."

| Field | Type | Notes |
|---|---|---|
| `name` | string \| null | |
| `description` | string \| null | |
| `images` | string[] \| null | Full replacement — send the new full list. |
| `category` | string \| null | |
| `status` | `ProductStatus` \| null | Only the three enum values above. |

Fields NOT editable via `patch` (surface to the owner, do not try to send them):
- `supplier_id`, `supplier_provider`, `supplier_product_id` — the supplier binding is fixed at create time.
- `variations` and anything inside them (`sku`, `attributes`, `available_quantity`, `purchase_price`, `shipping_cost`). To change variation structure or pricing, recreate the product.

---

## Delete

No body. Idempotence is not guaranteed — calling `delete` on an already-deleted id typically returns 404. Treat it as one-shot.

---

## Common validation pitfalls

- Sending `purchase_price` / `shipping_cost` as plain JSON numbers can work on create (the server accepts both), but **every read of the product returns them as strings**. Downstream code should not assume numeric type.
- `supplier_*` triple must match an existing connected integration; a fabricated `supplier_id` UUID will pass type validation and fail at the domain layer, often with a generic 422.
- `images` accepts any URL string; the server does not validate that the URL resolves or is an image. A broken URL will surface later during channel publishing.
- `status` enum is case-sensitive and lower-case.
