---
name: competitor-research
description: "Map competitors for a niche or product: SERP rivals, marketplace listings, active ads, and store deep dives. Use when the task says 'who else is selling X', 'show me competitors for niche Y', 'how do prices compare', 'how saturated is this market', 'who's running ads for this', 'analyze this competitor's store', or any task about identifying or sizing up competition. For keyword expansion use `keyword-research`; for buyer review themes use `product-demand-analysis`."
---

# Competitor Research Skill

## Goal

Gather competitive intelligence in a target niche: prefer **autonomous CLI** calls (`sellerclaw research-seo …`, `sellerclaw research-social …`) for SERP, marketplace, and ad-library signals; use **browser** for store deep dives when allowed.

## Modes

- **Autonomous (DataForSEO)**: `sellerclaw research-seo post-serp-competitors`, `… post-product-search` (Google Shopping), `… post-amazon-products` — body via `-b '<json>'`.
- **Autonomous (SociaVault)**: `sellerclaw research-social post-ad-library-search`, `… post-ad-library-company-ads` when `research_social` is active.
- **Assisted**: browser as below when proxy/browser is enabled.
- **Advisory**: high-level guidance if neither CLI nor browser is available.

## Ad Library research (SociaVault)

Use when `research_social` integration is active (CLI exit `2`/`503` otherwise). Prefer these **before** opening Meta or Google ad library UIs in the browser.

| Command | Use for |
|---|---|
| `sellerclaw research-social post-ad-library-search -b '<json>'` | Keyword search (`platform`: `facebook` or `google`, `query`) |
| `sellerclaw research-social post-ad-library-company-ads -b '<json>'` | Ads for a company (`platform`, `page_id`/`company_name` for Facebook; `domain`/`advertiser_id` for Google) |

## SERP research (DataForSEO)

Use when `research_seo` integration is active.

| Command | Use for |
|---|---|
| `sellerclaw research-seo post-serp-competitors -b '<json>'` | Domains competing on overlapping keywords |
| `sellerclaw research-seo post-product-search -b '<json>'` | Google Shopping titles, prices, sellers |
| `sellerclaw research-seo post-amazon-products -b '<json>'` | Amazon listing landscape for a query |

Body schemas: `sellerclaw describe <op_id>`.

## Research sources (via browser)

### 1. Google Search — find competitor stores

Search queries:

- `"{niche keyword}" site:myshopify.com`
- `"buy {product type}" -amazon -ebay -walmart`
- `"{niche keyword}" shop online free shipping`

Identify stores that appear to be independent Shopify/dropshipping operations (not major brands).

### 2. Store analysis

For each competitor store (visit via browser):

| Data point | What to look for |
|---|---|
| Product count | Approximate number of products in catalog |
| Price range | Lowest and highest price points |
| Product quality | Professional photos, detailed descriptions, reviews |
| Store quality | Custom domain, professional design, trust badges, policies |
| Shipping info | Stated delivery times, free shipping threshold |
| Niche focus | Single-niche vs general store |
| Tech signals | Shopify, WooCommerce, custom — check page source or footer |

### 3. Facebook Ad Library

Visit `https://www.facebook.com/ads/library/` via browser:

- Search by competitor store name or domain.
- Check for **active ads** (presence = niche is monetizable).
- Note creative formats: video, carousel, single image.
- Note copy themes: discount-focused, problem-solution, lifestyle, urgency.
- Check ad start dates: long-running ads likely indicate profitability.

### 4. Marketplace cross-reference

Check presence of similar products on:

- Amazon Best Sellers (relevant category page).
- TikTok Shop trending.
- AliExpress Hot Products / orders count.

Multi-platform presence confirms demand. Absence may indicate untapped opportunity or genuinely low demand — use trend data to disambiguate.

## Analysis framework

### Competitor signals

| Signal | Type | Indicates |
|---|---|---|
| Custom domain + professional design | Strength | Strong brand investment |
| 50+ products, curated catalog | Strength | Established operation |
| Active ads running 30+ days | Strength | Proven profitability |
| Customer reviews with photos | Strength | Real customer base |
| Fast stated shipping (7–14 days) | Strength | Premium supplier or local stock |
| Generic store name, default theme | Weakness | Low brand investment |
| Stock photos, no reviews | Weakness | Early stage or low quality |
| No active ads | Weakness | May not be actively selling |
| 25+ day shipping, no tracking info | Weakness | Poor supplier setup |
| Inconsistent pricing | Weakness | Unclear positioning |

### Gap identification

Look for gaps that represent positioning opportunities:

- **Product gaps**: categories competitors don't cover within the niche.
- **Price gaps**: no one offering mid-range or premium tier.
- **Quality gaps**: all competitors use low-effort product pages.
- **Audience gaps**: competitors target broad audience, specific segments underserved.
- **Creative gaps**: all ads use same format, opportunity for differentiation.

## Result format

For each analyzed competitor:

```
Store: {url}
Type: {shopify/woo/other} | Products: ~{count} | Price range: ${min}–${max}
Strengths: [{strength}]
Weaknesses: [{weakness}]
Active ads: {yes/no} | Running since: {date or "N/A"}
Ad themes: [{theme}]
```

Summary section:

```
Competitors analyzed: {N}
Market saturation: {low/moderate/high}
Gaps identified: [{gap description}]
Recommended positioning: {strategy}
```

## Progress checkpoints

If the task includes an `agent_task_id`, report progress via `sellerclaw agent-goals add-progress-note <task_id> -b '{"message":"…"}'` after:

1. **CLI / search phase** — list discovered competitor stores/domains with source (SERP, Shopping, Ad Library).
2. **Deep-dive phase** — per-store analysis summaries (strengths, weaknesses, ad status).

Include concrete data (URLs, product counts, price ranges) so results survive session timeouts.

## Scope limits by effort

Read the effort level from the Agent Task instructions (`Effort: QUICK/STANDARD/DEEP`). If not stated, use Standard.

| Limit | Quick | Standard | Deep |
|-------|-------|----------|------|
| Competitor deep-dives | 0 | 3 | 5-8 |
| Competitor surface scans | 0-3 (web search) | 5 (CLI/web search) | 10-15 (CLI + browser) |
| Ad library checks | 0 | 1 platform | Both Facebook + Google |
| Browser store visits | 0 | 0 (fallback only) | 5-8 (always for verification) |
| Price data sources | 1 | 1-2 | 3-4 (Amazon + Shopping + eBay + TikTok Shop) |

## Fallback when DataForSEO and SociaVault are unavailable

### Competitor count estimation via web search

1. `web_search`: "{niche keyword} site:myshopify.com" — result count approximates Shopify stores.
2. `web_search`: "buy {product} online -amazon -ebay -walmart" — count independent stores in results.
3. `web_search`: "{niche} dropshipping stores" — find lists and analyses.

### Price data extraction via web search

1. `web_search`: "{product} price comparison" — review sites with price tables.
2. `web_search`: "{product} site:amazon.com" — Amazon prices in snippets.
3. Tavily is preferred over Brave for price extraction (better fact extraction).

### Ad density via web search

1. `web_search`: "{niche} facebook ads competition" — marketing analyses.
2. `web_search`: "{niche} advertising spend ecommerce" — industry reports.
3. Browser fallback: facebook.com/ads/library, adstransparency.google.com.

When using web search fallbacks, note `"web_search"` in `data_sources_used` and describe which data points are estimates vs verified in `data_gaps`.

## Guardrails

- Scope limits are effort-dependent — see the "Scope limits by effort" section above.
- Always note the research date — competitor data is point-in-time.
- Do not scrape or extract proprietary data (customer lists, revenue, etc.).
- If a store requires login to view products, skip it and note the limitation.
- Report what you observe, not what you assume — clearly separate facts from inferences.
