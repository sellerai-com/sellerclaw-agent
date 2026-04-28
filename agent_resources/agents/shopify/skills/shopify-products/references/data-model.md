# Shopify — data models (`sellerclaw`)

Schemas: OpenAPI bundled with **`sellerclaw`** (`sellerclaw describe <operation_id>`).

**Rule:** Required vs optional keys **only under request bodies**. Response sections explain **meaning** for the agent, not JSON Schema minutiae.

Shopify ids may be **`string | integer`** where the bundle allows flexible wire encoding.

---

## `sellerclaw stores list-listings` — `ListingsResponse` / `ListingResponse`

### `ListingsResponse`

- **`items`** — Rows the adapter projects from **Shopify’s live catalog** (one line per variant): what is actually selling right now—not the internal draft-listing records.

### `ListingResponse`

- **`remote_id`** — Shopify **variant id** for this row—send to **`sync-stock`** / price updates when the task mutates marketplace state.
- **`sku`** — Merchant SKU on that variant—the join key to supplier rows and to **`sync-stock`** lines.
- **`title`** — Buyer-facing title as on the storefront (options may be folded into the text).
- **`price`** — Current sell price, **decimal string** in shop money.
- **`currency`** — ISO code for **`price`**.
- **`quantity`** — Sellable quantity the channel exposes for this variant **at read time**.
- **`image_url`** — Main image URL or `null` if Shopify has none for that variant.
- **`url`** — Storefront permalink or `null` (draft, headless, or no Online Store URL).
- **`is_variant`** — `true` when this row is a non-default variant line; helps explain duplicate titles/SKUs.
- **`extra`** — Nonstandard adapter keys—only read what a task explicitly references.

---

## `sellerclaw stores search-listings`

### `ListingSearchResponse`

- **`results`** — One entry per search batch the API returns (different `type`/`q` slices may appear as separate buckets).

### `ListingSearchResultResponse`

- **`search_type`** — Echo of the **`type`** parameter you passed—confirms SKU vs title (or other) search mode.
- **`query`** — Echo of **`q`**—helps debug unexpected empty results.
- **`listings`** — **`ListingResponse`** rows for that slice—same meaning as **`list-listings`**.

---

## `sellerclaw stores sync-stock`

### Request — `StockSyncRequest` / `StockSyncItemRequest`

**Body:** **`items`** — lines to reconcile; normally **≥ 1** element.

Per item:

- **`sku`** — Variant to target; must match Shopify’s SKU when SKU is how you key rows.
- **`quantity`** — Target **available** quantity after sync (≥ 0).
- **`remote_id`** — Exact Shopify variant id when **`sku`** is reused or ambiguous.
- **`price`**, **`compare_at_price`** — Optional repricing in the same call (`compare_at_price` = “was” price / strikethrough when used).

**Body (per item):** required **`sku`**, **`quantity`**; other keys optional.

### `BatchResultResponse`

- **`results`** — Per-line success payloads from the server (opaque).
- **`errors`** — Per-line failures—surface to the user with SKU/message for retry.

---

## Draft listings (`sellerclaw stores create-draft-listings`, `list-draft-listings`, `publish-draft-listings`)

### Request `ShopifyListingCreateRequest`

- **`product_ids`** — Internal SellerClaw catalog products that should gain **draft listing** shells for this Shopify channel.
- **`product_type`** — Optional Shopify **`product_type`** string forced onto the draft when taxonomy matters.

**Body:** **`product_ids`** required.

### Request `ShopifyListingPublishRequest`

- **`listing_ids`** — Internal draft listing UUIDs to publish/push to Shopify.

**Body:** **`listing_ids`** required.

### `ShopifyListingListResponse`

- **`items`** — Every listing row for the channel—draft vs linked vs failed publish.

### `ShopifyListingResponse`

- **`id`** — Primary key of the **listing row** in SellerClaw (not Shopify’s gid).
- **`product_id`** — Internal catalog product backing this listing.
- **`sales_channel_id`** — Same scope as **`store_id`** for this Shopify connection.
- **`product_type`** — Stored Shopify product type string used when publishing.
- **`status`** — Workflow phase (draft, publishing, live, error)— tells you whether Shopify ids exist yet.
- **`shopify_product_id`** — Numeric/string Shopify **product** id once linked—`null` until the remote product exists.
- **`variants`** — **`ShopifyListingVariantResponse`** rows—internal ↔ Shopify variant mapping + prices.
- **`created_at`**, **`updated_at`** — Audit timestamps for staleness checks.

### `ShopifyListingVariantResponse`

- **`product_variation_idx`** — Zero-based index of this variation inside the catalog product.
- **`supplier_variant_id`** — Dropship/supplier variant key for reconciliation.
- **`sku`** — SKU that will appear on Shopify for this variation.
- **`sell_price`** — Decimal-string channel sell price computed before publish.
- **`shopify_variant_id`** — Remote variant id after publish—`null` while still draft-only.

### `ShopifyListingBatchResult`

- **`results`** — Successful inputs—each **`ShopifyListingCreateResult`** wraps an updated snapshot.
- **`errors`** — **`BatchError`** entries for inputs that failed validation or Shopify.

### `ShopifyListingCreateResult`

- **`listing`** — Full **`ShopifyListingResponse`** after create/publish—persist **`listing.id`** for follow-up **`publish-draft-listings`** calls.

### `BatchError`

- **`message`** — Actionable explanation for operators.
- **`key`** — Optional fingerprint (SKU, batch key) tying the error back to a specific **`items`** row.
- **`details`** — Optional structured vendor/validation payload—inspect when **`message`** alone is insufficient.

---

## Batch Admin products (`sellerclaw stores create-shopify-products`, … `update-shopify-products`, `delete-shopify-products`, `publish-shopify-products`, `unpublish-shopify-products`)

### `ProductBatchCreateRequest`

- **`items`** — Independent **`ProductCreateItem`** payloads—each attempts one new Shopify Admin product.

### `ProductCreateItem`

- **`title`** — Mandatory storefront title—the minimum viable create.
- **`body_html`** — Long HTML description shown on the Online Store PDP.
- **`vendor`**, **`product_type`**, **`tags`** — Filtering, collections, and merchant taxonomy—not buyer SKU logic.
- **`status`** — Draft vs active; schema often defaults **`DRAFT`** so listings don’t accidentally go live before publishing workflows finish.
- **`images`** — Absolute URLs imported into Shopify’s media gallery.
- **`variants`** — **`ProductVariantCreate`** rows—every sellable SKU line.

**Body:** only **`title`** is required per item.

### `ProductVariantCreate`

- **`sku`** — Mandatory merchant SKU Shopify inventory apps key off.
- **`title`** — Variant-specific label (size/color).
- **`barcode`** — GTIN/EAN when tracked.
- **`price`**, **`compare_at_price`** — Selling price vs compare-at (“strike”) price for discounts.
- **`meta`** — **`DropshipVariantMeta`** for supplier linkage and landed cost—not shown to buyers.

**Body:** only **`sku`** is required per variant.

### `DropshipVariantMeta`

- **`supplier_variant_id`**, **`supplier_product_id`** — Foreign keys into supplier catalogs for automated PO/inventory joins.
- **`cost_price`** — Internal unit cost for margin calculations—distinct from Shopify compare-at price.

**Body:** all keys optional.

### `ProductBatchUpdateRequest`

- **`items`** — Array of **`ProductUpdateItem`** rows—each targets exactly **one** Shopify **`product_id`**.

### `ProductUpdateItem`

- **`product_id`** — Shopify Admin **product** id to mutate (**required** on each row).
- Other keys reuse **`ProductCreateItem`** semantics—include only deltas; **`null`/empty arrays** mean “clear” per Shopify patch rules.

### `ProductVariantUpdate`

- **`variant_id`**, **`sku`**, **`title`**, **`barcode`**, **`price`**, **`compare_at_price`**, **`meta`** — Patch only what changes; **`variant_id`** picks the variant when multiple move in one parent product.

**Body:** no required keys—pure patch object.

### `ProductBatchDeleteRequest`

- **`product_ids`** — Shopify product ids to **hard-delete** from Admin—irreversible in Shopify’s sense for that product record.

### `ProductBatchPublishRequest` / `ProductBatchUnpublishRequest`

- **`product_ids`** — Which Admin products gain/lose visibility on the selected publications (Online Store, apps, etc.).
- **`publication_names`** — `null` → default publications for the shop; non-null → limit to named publication handles.

Success body may still be `{}`—trust HTTP **200** / CLI exit code.

---

## `sellerclaw stores get-info` — `StoreInfoResponse`

- **`remote_id`** — Adapter’s stored Shopify shop id string.
- **`platform`** — Always **`shopify`** in this skill—guards mixed-channel tooling.
- **`name`** — Shop name shown to staff in Admin.
- **`seller_name`**, **`email`** — Integration contact metadata—do not assume it matches Shopify customer support email.
- **`currency`** — Shop **presentment** currency default for new prices.
- **`domain`** — Primary hostname (`*.myshopify.com` or verified custom domain).
- **`marketplace_id`** — Optional SellerClaw-specific market/region hint when present.
- **`extra`** — Key/value bag for adapter-only flags (timezone, plan hints)—read selectively.

---

## `sellerclaw stores proxy-shopify-*`

Response body is whatever **Shopify Admin REST** returns for the resource you addressed—field names follow [Shopify’s REST reference](https://shopify.dev/docs/api/admin-rest) for that resource path, not a SellerClaw wrapper.
