# eBay — data models (`sellerclaw`)

Schemas come from the OpenAPI bundle in **`sellerclaw-cli`**. If anything disagrees with production, **`sellerclaw describe <operation_id>`** wins.

**`store_id`** — SellerClaw sales-channel UUID (first positional argument on subcommands).

**Rule:** Required vs optional keys are stated **only for JSON request bodies** (`--json-body`).

---

## `sellerclaw ebay-shop get-account` → `EbayAccountResponse`

- **`username`** — The eBay seller username tied to this connection; use for reports and matching Seller Hub.
- **`marketplace_id`** — Regional site code (`EBAY_US`, …); drives currency, fees, category rules, and compliance for listings on this channel.
- **`sandbox`** — `true` means calls hit eBay’s sandbox—buyers and live reputation are unaffected.
- **`raw`** — Adapter-specific blob from the credential/account probe—debugging only; do not paste verbatim to end users.

---

## `sellerclaw ebay-listings list` → `EbayListingsResponse`

- **`--page-size`** — Caps rows returned in one invocation (**1–200**, default **200**).
- **`items`** — Listing rows from the marketplace adapter. Each element is an **open object** in OpenAPI—expect identifiers (SellerClaw listing id vs eBay item/offer ids), SKU, title, price, quantity, listing state, images; confirm keys from **`describe`** plus a real CLI response.

---

## `sellerclaw ebay-listings search`

### Request — `EbayListingSearchRequest`

**Body:** **`search_type`** is required: `sku` (seller SKUs) or `remote_id` (listing/item identifiers on eBay). **`search_values`** is optional: array of strings to look up in that mode.

- **`search_type`** — Chooses whether **`search_values`** are interpreted as SKUs or as remote listing/item ids.
- **`search_values`** — Bulk lookup list; empty-list behavior → **`describe`**.

### Response — `EbayListingSearchResponse`

- **`results`** — Rows that matched—same loose object shape as **`items`** from **`list`**. Use to resolve “which live listing row belongs to this SKU/id” before **`sync-stock`** or manual follow-up.

---

## `sellerclaw ebay-listings sync-stock`

### Request — `EbayStockSyncRequest`

**Body:** array **`items`**. Whether the body may omit **`items`** for a full-channel sync depends on deployment—check **`describe`**.

Per row:

- **`sku`** — Seller SKU on the offer line—the main key for stock pushes.
- **`quantity`** — Target **available** quantity to set (≥ 0).
- **`remote_id`** — Optional SellerClaw-side listing id when the same SKU appears on multiple offers.
- **`item_id`** — Optional eBay item id when you must disambiguate at the marketplace.

### Response — `EbayStockSyncResponse`

- **`results`** — Successfully applied rows (opaque; often updated qty plus touched ids).
- **`errors`** — Failed rows—drive **partial** outcomes; fix catalog or retry per line.

---

## `sellerclaw ebay-shop list-locations` → `EbayLocationListResponse`

- **`items`** — Registered **ship-from / merchant locations** (warehouse, store) used for fulfillment programs and inventory—review before **`create-location`** / **`delete-location`** and when tasks need “which address ships this”.

---

## `sellerclaw ebay-shop create-location`

### Request — `EbayCreateLocationRequest` / `EbayLocationAddress`

**Body:**

- **`merchant_location_key`** — Stable **1–36** character id—reused by **`delete-location`** and referenced by eBay merchant-location APIs.
- **`name`** — Human-readable label (e.g. warehouse name).
- **`address`** — Optional physical address block; omit when the workflow allows (see **`describe`**).

**`EbayLocationAddress`:**

- **`addressLine1`**, **`city`**, **`country`** — Minimum street + city + country (**`country`** usually ISO-3166).
- **`stateOrProvince`**, **`postalCode`** — Region and postal code when the country requires them.

### Response

Opaque JSON object (`additionalProperties`)—read whatever keys the adapter returns (eBay location id, validation, etc.).

---

## `sellerclaw ebay-shop get-or-create-fbz-location` → `EbayFBZLocationResponse`

- **`merchant_location_key`** — Key for the **Fulfillment by eBay (FBZ)** location this call ensures—wire into fulfillment policies when the task needs that program location.

---

## `sellerclaw ebay-shop delete-location`

On success the schema has **no response body**—rely on CLI exit code. **`merchant_location_key`** must match a key from **`list-locations`** or **`create-location`**.

---

## Request validation failures

Payload shape: **`detail`[]** with **`loc`** (JSON path into the body) and **`msg`** (reason). Fix **`--json-body`** and retry.
