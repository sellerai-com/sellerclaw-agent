# Product data model

## Product response (`list` / `get`)


| Field                 | Type                         | Notes                                                                                 |
| --------------------- | ---------------------------- | ------------------------------------------------------------------------------------- |
| `id`                  | uuid                         | Primary identifier. Pass as `product_id` everywhere.                                  |
| `user_id`             | uuid                         | Owning workspace user.                                                                |
| `supplier_id`         | uuid \| null                 | Connected supplier account, or `null` for supplier-less (user-defined) products.      |
| `supplier_provider`   | string \| null               | Provider code, e.g. `cj`. `null` for supplier-less products.                          |
| `supplier_product_id` | string \| null               | Supplier-side product id. `null` for supplier-less products.                          |
| `name`                | string                       | Canonical product name.                                                               |
| `description`         | string                       | Canonical description.                                                                |
| `images`              | string[]                     | Image URLs (first = hero).                                                            |
| `category`            | string                       | Taxonomy path.                                                                        |
| `status`              | `ProductStatus`              | See enum below.                                                                       |
| `variations`          | `ProductVariationResponse[]` | One entry per SKU.                                                                    |
| `created_at`          | datetime (ISO 8601)          |                                                                                       |
| `updated_at`          | datetime (ISO 8601)          | Bumped on any server-side change.                                                     |


`ProductStatus`: `sourced` = saved from supplier search, not active yet; `active` = live catalog item eligible for listings; `archived` = retired, do not publish or send to supplier.

### Variation response


| Field                 | Type               | Notes                                                    |
| --------------------- | ------------------ | -------------------------------------------------------- |
| `supplier_variant_id` | string             | Supplier-side variant id for supplier-bound rows; an internal unique key per variation for supplier-less rows. |
| `sku`                 | string             | Internal SKU.                                            |
| `name`                | string             | Variant label.                                           |
| `images`              | string[]           | Variant-specific images (may be empty).                  |
| `attributes`          | `{string: string}` | Option map, e.g. `{"color": "red", "size": "M"}`.        |
| `available_quantity`  | integer ≥ 0        | Supplier stock.                                          |
| `purchase_price`      | decimal **string** | Supplier unit cost; signed decimal string, not a number. |
| `shipping_cost`       | decimal **string** | Supplier shipping for the target market; same format.    |


---

## Create request — `batch-create`

Body: `{"items": ProductCreate[]}`. One batch call is all-or-nothing **per item**.

`ProductCreate` always required: `name`, `description`, `category`, `variations` (`ProductVariationCreate[]`, ≥1).

Supplier binding (`supplier_id` uuid, `supplier_provider` string, `supplier_product_id` string) is **optional but all-or-nothing**: send all three for dropshipping rows, omit all three for user-defined rows. Sending some but not all → HTTP 422.

Optional: `images` (`string[]`; strongly recommended, because downstream listings reject products without images).

`ProductVariationCreate` required: `supplier_variant_id`, `sku`, `name`, `available_quantity` (integer ≥ 0), `purchase_price`, `shipping_cost` (numbers or decimal strings; server normalises to decimal strings). Optional: `images` (`string[]`), `attributes` (`{string: string}`).

For supplier-less rows, `supplier_variant_id` is any unique internal string per variation (e.g. `var-1`, `var-2`) — uniqueness is checked within the product only.

---

## Patch request — `patch`

Body: `ProductPatchRequest`. Omit a field to leave it unchanged.

Editable: `name`, `description`, `category` (`string | null`), `images` (`string[] | null`, full replacement), `status` (`ProductStatus | null`).

Not editable via `patch`; tell the owner instead of sending:

- `supplier_id`, `supplier_provider`, `supplier_product_id` — the supplier binding is fixed at create time.
- `variations` and anything inside them (`sku`, `attributes`, `available_quantity`, `purchase_price`, `shipping_cost`). To change variation structure or pricing, recreate the product.

---

## Common validation pitfalls

- `supplier_*` must match an existing connected integration when present; a fabricated `supplier_id` can pass type validation and fail later. To skip supplier binding entirely, omit **all three** `supplier_*` keys.
- Sending only some `supplier_*` keys (e.g. just `supplier_provider`) fails with HTTP 422 — the three are validated as a tuple.
- `images` accepts any URL string; the server does not validate that the URL resolves or is an image. A broken URL will surface later during channel publishing.
- `status` enum is case-sensitive and lower-case.
- Supplier-less rows are **not** polled for stock/price by the periodic supplier sync — keep them in `active` only as long as the owner intends to honour the current `available_quantity` and `purchase_price`. Update via *recreate* when stock or price changes.

---

## Relationships and published listings

Read when debugging catalog ↔ store ↔ order flows:

```text
Sales Channel (id = store_id)
  ├─ Orders (sales_channel_id) → line items: product_id → Product; supplier_variant_id → variation
  └─ Published listings (sales_channel_id) → product_id + supplier_variant_id + sku + remote_id

Product
  └─ variations[]: supplier_variant_id, sku, pricing, stock
```

**Published listing** links a product variation to a marketplace listing and drives stock/price sync. Typical fields: `product_id`, `sales_channel_id`, `supplier_variant_id`, `sku`, `remote_id` (marketplace listing id; `null` while pending). For full store API fields, use `sellerclaw list-operations --search listing` and `describe`.