---
name: product-enrichment
description: Enrich an incomplete product card (brand, model, or GTIN) with structured data from external catalogs. Use when the user wants to publish a product of a known brand but has only partial info, and you need title, brand, MPN, GTIN, images, price reference, and category. Primary source is the free eBay Browse API, with open-source fallbacks.
---

# Product Enrichment Skill

## Goal

Turn sparse product input (brand + model, or a barcode) into a publish-ready card: `title`, `brand`, `mpn`, `gtin`, `category_path`, thumbnail URL, and a market-price reference. Default to the free eBay Browse endpoint; use other sources only when eBay's coverage is thin.

## Base URL and authentication

- Base URL: `{{api_base_url}}`
- Header: `Authorization: Bearer $AGENT_API_KEY`
- All bodies below are JSON (`Content-Type: application/json`).

## When to use

- User asks to "publish product X of brand Y" without providing a full card.
- A barcode (EAN/UPC/GTIN/ISBN) is given but other fields are missing.
- A quick market reference (price / sellers / category) is needed for a known item.

**Do not use for:**
- niche/trend discovery → `trend-analysis` / `niche-data-collection`;
- keyword or competitor SEO analysis → `keyword-research` / `competitor-research`;
- scraping arbitrary brand pages → `web_fetch` + JSON-LD extraction directly.

## Data sources (priority)

1. **eBay Browse API** — built-in endpoint, free, primary source. Broad coverage of mass brands (electronics, apparel, beauty, collectibles).
2. **Open barcode databases** (Open Food Facts, Open Products Facts, Wikidata P3962) — fall back when a GTIN is known and eBay returns nothing.
3. **`web_search` (Brave/Tavily) + `web_fetch` JSON-LD** — universal fallback via a retailer page. Use when the first two are too thin.
4. **`browser`** — only if JSON-LD is absent on the page. Heavyweight; last resort.

## Primary endpoint: eBay Browse Search

`POST {{api_base_url}}/research/catalog/ebay/search` — searches eBay by keyword and/or GTIN and returns enriched product identifiers (brand, MPN, GTIN, EPID).

### Request body

| Field | Type | Description |
|---|---|---|
| `query` | `string \| null` | Free text: brand + model + (optional) attributes. Omit if `gtin` is set. |
| `gtin` | `string \| null` | Barcode (EAN/UPC/GTIN/ISBN). Digits only, 8–14 chars. Omit if `query` is set. |
| `marketplace_id` | `string` | `EBAY_US` (default), `EBAY_GB`, `EBAY_DE`, `EBAY_FR`, `EBAY_IT`, `EBAY_ES`, `EBAY_NL`, `EBAY_AU`, `EBAY_CA`, `EBAY_PL`, `EBAY_IN`. |
| `limit` | `int` | 1–200, default 20. |
| `condition_new_only` | `bool` | If `true`, drops used/refurbished listings. For new-product publishing, usually `true`. |

At least one of `query` or `gtin` is required.

### Response (truncated)

```json
{
  "provider": "ebay_browse",
  "available_providers": ["ebay_browse"],
  "query": "Nike Air Max 90",
  "gtin": null,
  "marketplace_id": "EBAY_US",
  "total": 124,
  "items": [
    {
      "item_id": "v1|...|0",
      "epid": "...",
      "title": "Nike Air Max 90 ...",
      "brand": "Nike",
      "mpn": "AM90-001",
      "gtin": "0012345678905",
      "condition": "NEW",
      "category_id": "15709",
      "category_path": "Clothing, Shoes & Accessories > Shoes",
      "price": "129.99",
      "currency": "USD",
      "item_web_url": "https://www.ebay.com/itm/...",
      "thumbnail": {"url": "https://...", "height": 300, "width": 300},
      "seller": {"username": "...", "feedback_percentage": "99.2", "feedback_score": 10500}
    }
  ]
}
```

### Error codes

| Status | When | What to do |
|---|---|---|
| 400 | Neither `query` nor `gtin`; invalid `gtin` | Fix the request. |
| 502 | eBay returned an API error | Retry without `condition_new_only` or rephrase `query`. If still failing — switch to a fallback source. |
| 503 | eBay credentials are not configured on the platform | Tell the user the source is unavailable and use a fallback. |

## Recommended pipeline

1. **If GTIN is known** — call with `gtin=<code>`, `condition_new_only=true`, `limit=5`. If 1+ item returns — pick the first one that has non-empty `brand`/`mpn`/`title` and use its fields as the card seed.
2. **If no GTIN but brand + model** — `query="<brand> <model>"`, `condition_new_only=true`, `limit=10`. Deduplicate by `epid`.
3. **Merge.** From the first relevant item take: `title`, `brand`, `mpn`, `gtin`, `category_path`, `thumbnail.url`, `item_web_url` (as a validation reference), `price`/`currency` (as a market-price reference, not MSRP).
4. **If zero items or fields are thin** — move to fallback sources (Open*Facts, Wikidata, or `web_search` + `web_fetch` on a retailer page with JSON-LD extraction).

## Guardrails

- eBay Browse returns **active listings**, not a canonical catalog. If the brand is not sold on eBay, the response will be empty. Do not treat eBay price as MSRP.
- Requests to eBay Browse are **not billed** and **not cached** on our side — retry if needed.
- Coverage is generally strong for apparel / electronics / collectibles; weaker for premium beauty / niche B2B — be ready to fall back.
- Do not confuse with `src/ebay_app/` or seller OAuth — those are for listing products on eBay as a seller. This endpoint uses a platform app token; the user does not need to connect their eBay account.
