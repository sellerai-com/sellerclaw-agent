---
name: product-search
description: "Look up product data on the supplier side — keyword search, fetch product / variant / stock details, and quote shipping at a connected supplier. Use when the delegated task says 'find products at the supplier', 'how much does X cost at CJ', 'is this in stock at the supplier', 'how long is shipping', 'check the supplier for SKU Y', 'refresh this supplier product', 'quote shipping for this item', or any task that needs product information from a supplier integration. Not for placing purchases — use `order-fulfillment`."
---

# Supplier product search

Catalog-side operations at a connected supplier — search, product / variant / stock lookups, shipping quotes. All calls go through the `sellerclaw` CLI; for the full body / response schema of any command run `sellerclaw describe <operation_id>`.

## Common shape

- Every command takes the provider id as the first positional argument (e.g. `cj`). Pull it from the brief or from the catalog row you are looking up — never hard-code one.
- All commands accept `--user-id <uuid>` to scope the call to a specific workspace user (omit unless the brief tells you to).
- Stock is checked **per variant** (variant id), not per product. There is no batch stock endpoint — call once per candidate variant.
- Shipping quote requires a concrete `shipping_method` (see **Provider notes**) and a complete `shipping_address`.
- Response shapes are provider-specific; treat the fields listed below as the common contract and pass any extra fields through to the caller as-is.
- If the briefed provider is not documented under **Provider notes**, fall back to `sellerclaw describe` and treat any provider-specific defaults (shipping methods, address fields, stock semantics) as unknown.

---

## Commands

### `search-products` — search the catalog by keyword

**Command:** `sellerclaw suppliers search-products <provider> --query "<text>" [--page N] [--page-size N]`

**Parameters:**

- `<provider>` (positional, required) — provider id (e.g. `cj`).
- `--query` (required, string) — keyword search; phrases are passed verbatim.
- `--page` (optional, integer, default `1`) — 1-based page number.
- `--page-size` (optional, integer, default `20`, max `200`) — items per page.

**Response (typical fields):**

- `products[]` — per item:
  - `product_id` — feed into `get-product` / `get-variants`.
  - `name`, `images[]`, `source_price` (or price range).
  - Provider-specific flags (e.g. CJ `listed`) — pass through.
- `total`, `page`, `page_size` — pagination meta.

**Notes:**

- One query per intent; do not loop over keyword variations.
- For triage use `--page-size 10` and bump only when the brief asks for breadth.

### `get-product` — full product card

**Command:** `sellerclaw suppliers get-product <provider> <product_id>`

**Parameters:**

- `<provider>` (positional, required).
- `<product_id>` (positional, required) — id from `search-products` (`products[].product_id`).

**Response (typical fields):**

- `product_id`, `name`, `description` (may be HTML — strip before re-using), `images[]`, `category`, base `price` / range, optional `weight` / dimensions.
- Variants may be embedded or omitted depending on provider — call `get-variants` when you need them with current stock.

### `get-variants` — variant list for a product

**Command:** `sellerclaw suppliers get-variants <provider> <product_id>`

**Parameters:**

- `<provider>` (positional, required).
- `<product_id>` (positional, required).

**Response (typical fields):**

- `variants[]` — per item:
  - `variant_id` — feed into `check-stock`, `calculate-shipping`, and `order-fulfillment`.
  - `sku`, attribute map (option name → value, e.g. `Color: Black`, `Size: M`).
  - `price` (decimal string), `image`, optional `stock` / availability flag.
- Stock here may be stale or missing — confirm with `check-stock` before quoting or purchasing.

### `check-stock` — availability for one variant

**Command:** `sellerclaw suppliers check-stock <provider> <variant_id>`

**Parameters:**

- `<provider>` (positional, required).
- `<variant_id>` (positional, required) — id from `get-variants` (`variants[].variant_id`).

**Response (typical fields):**

- `variant_id`, `available` (bool) and / or `quantity` (integer).

**Notes:**

- One call per variant; limit to actual candidates.
- If `quantity` is missing but `available` is true, treat as "in stock, unverified quantity" and flag it in the result.

### `calculate-shipping` — shipping cost and ETA quote

**Command:** `sellerclaw suppliers calculate-shipping <provider> --json-body '<JSON>'`

`--json-body` accepts a JSON literal, `@/path/to/file.json` for a file, or `@-` for stdin. Prefer the file form for multi-item bodies.

**Body** (`CreateOrderRequestSchema` — the same shape `create-order` uses; payment fields are ignored for quotes):

- `items` (array, required in practice — without it the response carries no quote) — per item:
  - `variant_id` (required, string) — id from `get-variants`.
  - `quantity` (required, integer, > 0).
  - `shipping_method` (required, string) — provider-specific identifier; see **Provider notes**.
- `shipping_address` (object, **required**) — every field is a required string:
  - `country_code` — ISO-3166 alpha-2 (e.g. `US`, `DE`).
  - `province` — state / region code or name accepted by the provider.
  - `city`.
  - `zip_code`.
  - `address_line` — single street line.
  - `full_name`, `phone` — recipient contact (placeholder values are fine for quotes).
- `pay_type` (optional, integer 1–3, default `2`) — consumed only by `create-order`; ignored on shipping quotes (safe to omit).

**Response (typical fields):**

- One entry per offered shipping option: `shipping_method`, `cost` (decimal string) + `currency`, `min_days` / `max_days` ETA, optional carrier hint.

**Example body:**

```json
{
  "items": [
    {"variant_id": "abc-123", "quantity": 1, "shipping_method": "CJPacket"}
  ],
  "shipping_address": {
    "country_code": "US",
    "province": "CA",
    "city": "Los Angeles",
    "zip_code": "90001",
    "address_line": "1 Test St",
    "full_name": "Test",
    "phone": "+10000000000"
  }
}
```

---

## Provider notes

### CJ Dropshipping — `cj`

- **Shipping methods** — common: `CJPacket` (preferred for US), `ePacket`, `USPS`. If unsure, start with `CJPacket`; on `422` retry with `ePacket`.
- **Address shape** — all `shipping_address` fields are required; `province` accepts US two-letter state codes (`CA`, `NY`) and full names for non-US.
- **Stock** — `quantity` is aggregated across all CJ warehouses (CN/US/EU/…); `available=true` means at least one warehouse has positive inventory. `quantity` may still be missing on rare responses — treat that case as "available, unverified" and flag it.
- **Variant id format** — CJ's real `variant_id` is a long numeric string (e.g. `1405792589029969920`). The short `CJGX…` / `CJGY…` code returned in product fields is a **product-level SKU**, not a variant id; passing it to `check-stock` will yield `Variant not found` (CJ error `1602000`). Always use the `variant_id` from `get-product` / `get-variants` output.
- **`Variant not found` (code `1602000`)** — surfaces as an error, not as `available=false`. It means the `variant_id` is invalid; re-fetch variants for the product instead of retrying.
- **Search relevance** — queries longer than ~3 words drift quickly; prefer 1–2 keywords.

<!-- Add a new `### <Provider name> — <id>` subsection here when another supplier is connected. Cover at minimum: supported shipping method names, address quirks, stock-field quirks, search hints. -->

---

## Failure handling

- CLI call fails → retry at most twice; on persistent failure → return a blocker with the failed command and error message.
- `422` from `calculate-shipping` → most often an unsupported `shipping_method` or an invalid address field; surface the validation message to the caller before retrying.
- Provider not connected → return a blocker.
- Provider not documented in **Provider notes** → use `sellerclaw describe` to learn the body shape; if anything is ambiguous (shipping method names, address fields, stock semantics), return a blocker rather than guess.
