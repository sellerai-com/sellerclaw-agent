---
name: niche-data-collection
description: Collect structured research data for niche evaluation when delegated by supervisor. Return raw data as JSON — supervisor owns scoring.
---

# Niche Data Collection Skill

## Goal

Collect raw research data for niche evaluation sub-tasks delegated by supervisor.
Return structured JSON — **do not score or compute composites**. Supervisor applies
scoring rubrics and calculates the composite Niche Score.

## When to use

Supervisor delegates focused data-collection Agent Tasks for niche analysis. Each
task specifies which data to collect, which tools to use, and the expected return
format. This skill defines the consistent structure for those returns.

## API parameter quick reference

Exact parameter names for commonly used endpoints. Full docs in referenced skills.

| Endpoint | Method | Key params | Notes |
|----------|--------|-----------|-------|
| `/research/trends/interest-over-time` | GET | `keywords` (plural, comma-separated), `timeframe`, `geo` | NOT `keyword` |
| `/research/trends/related-queries` | GET | `keyword` (singular), `timeframe`, `geo` | NOT `keywords` |
| `/research/trends/trending` | GET | `geo`, `hours` | |
| `/suppliers/{provider}/products` | GET | `query`, `page_size` | NOT `q`, NOT `/products/search` |

**Common mistakes:** `interest-over-time` uses `keywords` (plural); `related-queries` uses `keyword` (singular). Supplier search param is `query` not `q`, endpoint is `/products` not `/products/search`.

## Sub-task types and return formats

### Type 1: Demand & Trends

Collect trend direction, search volume, marketplace presence, and social signals.

**Data collection order:**
1. Google Trends (interest-over-time, related-queries) — available in Tier 1-3
2. DataForSEO keyword-trends / keyword-volume — if `research_seo` active
3. SociaVault TikTok/Reddit — if `research_social` active
4. Web search for volume estimates — fallback when DataForSEO unavailable
5. Web search for Amazon/marketplace data — fallback when DataForSEO unavailable

**Return format:**
```json
{
  "trend_direction": "rising|stable|declining|seasonal",
  "growth_rate_12m_pct": null,
  "current_interest_level": 0,
  "search_volume_monthly": null,
  "search_volume_source": "dataforseo|web_search_estimate|unavailable",
  "cpc_usd": null,
  "rising_related_queries": [],
  "seasonality": "none|mild|strong",
  "peak_months": [],
  "amazon_listing_count": null,
  "amazon_price_range": {"min": null, "max": null, "median": null},
  "tiktok_engagement": "high|medium|low|unavailable",
  "reddit_mentions": "frequent|some|rare|unavailable",
  "data_sources_used": [],
  "data_gaps": []
}
```

### Type 2: Competition & Pricing

Collect SERP competitors, marketplace pricing, ad density, and top competitor profiles.

**Data collection order:**
1. DataForSEO serp-competitors / product-search / amazon-products — if `research_seo` active
2. SociaVault ad-library-search — if `research_social` active
3. Web search for competitor stores, pricing, ad analysis — fallback
4. Browser for store deep-dives, Facebook Ad Library — if available

**Return format:**
```json
{
  "serp_competitor_count": null,
  "serp_difficulty": null,
  "google_shopping_seller_count": null,
  "retail_price_range": {"min": null, "max": null, "median": null, "currency": "USD"},
  "price_spread_ratio": null,
  "premium_segment_exists": false,
  "active_ad_count": {"facebook": null, "google": null},
  "ad_density": "high|medium|low|unavailable",
  "top_competitors": [],
  "data_sources_used": [],
  "data_gaps": []
}
```

### Type 3: Supplier & Cost

Find suppliers, compare pricing, estimate shipping cost and time.

**Data collection order:**
1. Supplier API (`/suppliers/{provider}/products`, variants, stock, shipping) — if available
2. Web search for wholesale/dropshipping pricing — fallback
3. Web search for AliExpress/CJ indexed pages — fallback
4. Browser for supplier sites — if available
5. Reverse calculation from retail price / 2.5-3x — last resort

**Return format:**
```json
{
  "supplier_available": false,
  "supplier_source": "cj_api|aliexpress_web|wholesale_estimate|unavailable",
  "sku_count": null,
  "supplier_cost_range": {"min": null, "max": null, "avg": null, "currency": "USD"},
  "best_candidate": {
    "product_id": "",
    "name": "",
    "cost": null,
    "variants_count": null,
    "in_stock": false,
    "stock_quantity": null,
    "has_images": false
  },
  "shipping": {
    "method": "",
    "cost_usd": null,
    "days_min": null,
    "days_max": null,
    "source": "api_calc|web_estimate|default_assumption"
  },
  "moq": null,
  "data_sources_used": [],
  "data_gaps": []
}
```

### Type 4: Deep-Dive (conditional, browser-based)

Browser-intensive research for competitor store analysis, ad library, and
certification/regulatory checks.

**Return format:**
```json
{
  "competitor_deep_dives": [],
  "ad_library": {"active_count": null, "longest_running_days": null, "themes": []},
  "certifications_needed": [],
  "shipping_restrictions": [],
  "notes": ""
}
```

### Type 5: Customer Voice (deep only)

Collect customer praise/complaint themes, positioning angles, and buyer questions.
Only used in deep mode.

**Data collection order:**
1. DataForSEO amazon-reviews for top ASINs — if `research_seo` active
2. DataForSEO people-also-ask — if `research_seo` active
3. SociaVault TikTok Shop reviews / Reddit search — if `research_social` active
4. Web search for complaints, problems, reviews — fallback

**Return format:**
```json
{
  "customer_praise_themes": [],
  "customer_complaint_themes": [],
  "positioning_angles": [],
  "paa_questions": [],
  "tiktok_shop_review_themes": [],
  "reddit_discussion_themes": [],
  "data_sources_used": [],
  "data_gaps": []
}
```

### Type Combined (quick mode only)

Union of Type 1 + Type 2 + Type 3 fields in one flat JSON object. Omit Type 4/5 fields.
Single `data_sources_used` and `data_gaps` at top level (not per-type).

## Fallback chains

Fallback chains are defined in each referenced skill: `trend-analysis`, `competitor-research`, `social-trend-discovery`, `supplier-matching`, `product-demand-analysis`. Try primary source first; fall back only on failure/unavailability.

## Data quality markers

Every return must include:

- **`data_sources_used`**: list of actual API endpoints or methods called (e.g.
  `"GET /research/trends/interest-over-time"`, `"web_search"`, `"browser"`)
- **`data_gaps`**: list of data points that could not be collected and why (e.g.
  `"search_volume: DataForSEO unavailable, estimated via web search"`)

These markers allow supervisor to set appropriate confidence levels.

## Effort-dependent behavior

Read `Effort: QUICK/STANDARD/DEEP` from task instructions (default: STANDARD).
Follow stated budgets strictly.

### Data strategy summary

| Strategy | Quick | Standard | Deep |
|----------|-------|----------|------|
| Sources per metric | 1 (fastest) | 1 (best available) | 2-3 (triangulate) |
| Fallback chain | Only on failure | On failure/unavailability | Always use multiple |
| Browser | Never | Fallback only | Always for verification |
| Keyword variations | 1-2 seeds | 3-5 seeds | 5-10 seeds + long-tail |

## API call budget per sub-task

| Sub-task type | Quick | Standard | Deep |
|---------------|-------|----------|------|
| Type 1 (Demand & Trends) | 3-4 | 8-12 | 15-25 |
| Type 2 (Competition & Pricing) | 2-3 | 8-15 | 20-30 |
| Type 3 (Supplier & Cost) | 1-2 | 8-12 | 15-25 |
| Type 4 (Deep-Dive) | N/A | 3-5 browser | 5-8 browser |
| Type 5 (Customer Voice) | N/A | N/A | 8-12 |
| Combined (quick only) | 5-8 | N/A | N/A |

- If exceeding budget, stop and report partial results — supervisor works with
  what is available.
- Prefer API over web search over browser for speed and structure.

## Progress checkpoints

If the task includes an `agent_task_id`, report progress after completing each
major section via `POST /goals/agent-tasks/{agent_task_id}/progress`. Include
concrete data points (not just status labels) so results survive session timeouts.

## Guardrails

- Never fabricate data. If a data point is unavailable, return `null` and add to
  `data_gaps`.
- Never compute scores — supervisor owns scoring.
- Never present a summary or recommendation — return raw structured data only.
- Retry a failed API call at most twice; then mark the data point as unavailable.
- Follow the return format exactly — supervisor parses JSON from the outcome.
