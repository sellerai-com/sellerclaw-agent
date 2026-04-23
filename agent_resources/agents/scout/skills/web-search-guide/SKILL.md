---
name: web-search-guide
description: "Reference for web_search tool parameters, browser snapshot usage, and fallback strategies to avoid common errors."
---

# Web Search & Browser Guide

## Goal

Avoid common errors when using `web_search`, `web_fetch`, and `browser` tools. Use this skill as a reference before making search or browser calls.

## web_search parameters

| Parameter | Format | Example | Notes |
|-----------|--------|---------|-------|
| `query` | Plain text string | `"sphynx cat products dropshipping"` | Required |
| `search_lang` | ISO 639-1 two-letter code | `"en"`, `"de"`, `"fr"` | **NOT** locale codes like `"en-US"` or `"en-GB"` |
| `country` | ISO 3166-1 alpha-2 | `"US"`, `"GB"`, `"DE"` | Filters results by country |
| `count` | integer | `10` | Max results to return |

### Common mistakes

- `search_lang: "en-US"` → **WRONG**. Use `search_lang: "en"`.
- `search_lang: "en-gb"` → **WRONG**. Use `search_lang: "en"` + `country: "GB"`.
- Omitting `search_lang` entirely is fine — it defaults to English.

## Effective query patterns

### For niche research

| Data needed | Query pattern |
|-------------|---------------|
| Search volume estimates | `"{niche}" search volume monthly 2026` |
| Amazon presence | `"{niche}" site:amazon.com` |
| Wholesale/supplier prices | `"{niche}" wholesale price dropshipping` |
| Competition level | `"{niche}" shop` or `"{niche}" store online` |
| Ad activity | `"{niche}" facebook ads` |
| Certification/compliance | `"{niche}" FCC certification required` |
| Shipping restrictions | `"lithium battery shipping restrictions dropshipping"` |

### Tips

- Quote the niche name to get exact-phrase matches.
- Add `site:` operator to restrict to specific domains (amazon.com, aliexpress.com).
- Add the current year for freshness (e.g. `"... 2026"`).
- For price data, add `"price"` or `"$"` to the query.

## web_fetch usage

Use `web_fetch` to load full page content after finding URLs via `web_search`.

- Pass the exact URL from search results — do not guess or construct URLs.
- If the page returns an error (403, 404, etc.), try an alternative source.
- Some pages block automated access — fall back to `browser` if web_fetch fails.

## browser tool

### browser.snapshot

| Parameter | Value | Notes |
|-----------|-------|-------|
| `action` | `"snapshot"` | Returns page structure as accessible tree |
| `refs` | `"role"` | **Use `"role"` always.** `"aria"` requires Playwright `_snapshotForAI` support which may not be available. |

### Common mistakes

- `refs: "aria"` → may fail with `"Error: refs=aria requires Playwright _snapshotForAI support"`. Always use `refs: "role"`.
- Not taking a snapshot before interacting — always snapshot first to discover element refs.

### Browser workflow

1. `browser.navigate` to the target URL.
2. `browser.snapshot` with `refs: "role"` to discover elements.
3. Interact using refs from the snapshot (click, fill, etc.).
4. Snapshot again after interactions to verify state.

## Fallback strategy

When a primary data source fails, follow this cascade:

```
DataForSEO API → web_search → web_fetch → browser → report as data_gap
```

### Specific fallbacks

| Failed source | Fallback |
|---------------|----------|
| DataForSEO `keyword-volume` | `web_search "{niche} search volume monthly"` → extract from SEO articles |
| DataForSEO `amazon-products` | `web_search "{niche} site:amazon.com"` → count results, extract prices |
| DataForSEO `serp-competitors` | `web_search "{niche} shop"` → count independent stores vs major retailers |
| Supplier API `/products` | `web_search "{niche} wholesale price dropshipping"` or `web_search "{niche} site:aliexpress.com"` |
| SociaVault TikTok/Reddit | `web_search "{niche} tiktok trend"` or `web_search "{niche} reddit discussion"` |
| `web_fetch` blocked | `browser.navigate` + `browser.snapshot` to extract data |

### Reporting data gaps

Always include failed sources in the `data_gaps` array of your return JSON:

```json
{
  "data_gaps": [
    "DataForSEO keyword-volume: empty response for query",
    "SociaVault: TikTok endpoint returned 502"
  ]
}
```
