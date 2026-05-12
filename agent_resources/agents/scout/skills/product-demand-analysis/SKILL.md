---
name: product-demand-analysis
description: "Validate real demand and customer voice for a specific product or shortlisted niche — pull marketplace listings, reviews, buyer questions, and sentiment. Use when the task says 'do people actually buy this', 'show me reviews for X', 'what do buyers complain about', 'should I list this — go or no-go', 'find proof of demand', 'check buyer sentiment', or any task asking for buyer-side evidence on a shortlisted product/niche (SKU, niche name, or hero keyword required). For top-of-funnel niche discovery use `trend-analysis`; for competitor stores/ads use `competitor-research`."
---

# Product Demand Analysis Skill

## Goal

After initial screening, validate **real demand and customer voice** for a specific product or niche using marketplace data, People Also Ask, and content sentiment via the `sellerclaw` CLI.

## When to use

- You already have a shortlist (SKU, niche name, or hero keyword).
- You need Amazon/Google Shopping proof points, review themes, and SERP questions.

## Data sources (CLI)

| Command | Body focus | Output use |
|---|---|---|
| `sellerclaw research-seo post-amazon-products -b '<json>'` | `keyword` / filters | Competitor ASINs, prices, titles |
| `sellerclaw research-seo post-amazon-reviews -b '<json>'` | `asin` | Review text themes, star mix |
| `sellerclaw research-seo post-people-also-ask -b '<json>'` | `keyword`, `location_code` | Buyer questions for copy and positioning |
| `sellerclaw research-seo post-content-sentiment -b '<json>'` | `keyword` or text sample | Media/social polarity buckets |
| `sellerclaw research-social post-tiktok-shop-search -b '<json>'` | `query`, optional `page` | TikTok Shop listing landscape |
| `sellerclaw research-social post-tiktok-shop-reviews -b '<json>'` | `url` and/or `product_id` | Social-native review themes |
| `sellerclaw research-social post-reddit-search -b '<json>'` | `query` | Community pain points and language |

Use TikTok Shop and Reddit when `research_social` (SociaVault) is configured. Body schemas: `sellerclaw describe <op_id>`.

Example (PAA):

```bash
sellerclaw research-seo post-people-also-ask -b '{
  "keyword": "ergonomic desk mat",
  "location_code": 2840,
  "language_code": "en"
}'
```

## Report structure

1. **Market snapshot** — top Amazon + Shopping listings (price band, dominant brands); add TikTok Shop if available.
2. **Customer voice** — recurring praise/complaints from reviews (Amazon + optional TikTok Shop); supplement with Reddit themes when useful.
3. **Questions** — PAA themes mapped to positioning angles.
4. **Risk** — sentiment negatives or saturated narratives.

## Progress checkpoints

If the task includes an `agent_task_id`, report progress via `sellerclaw agent-goals add-progress-note <task_id> -b '{"message":"…"}'` after collecting the market snapshot (Amazon/Shopping/TikTok listings and price bands). Include concrete data (top ASINs, price ranges, brand names) so results survive session timeouts.

## Scope limits by effort

Read the effort level from the Agent Task instructions (`Effort: QUICK/STANDARD/DEEP`). If not stated, use Standard.

| Limit | Quick | Standard | Deep |
|-------|-------|----------|------|
| Amazon reviews analyzed | 0 | 0 | Top 3 products |
| PAA questions collected | 0 | 0 | Yes |
| TikTok Shop reviews | 0 | 0 | Top products |
| Marketplaces checked | 1 | 2 | 3-4 |
| Content sentiment | 0 | 0 | Yes (`post-content-sentiment`) |

This skill is primarily used in deep mode (Agent Task 5: Customer Voice). In quick/standard mode, marketplace data is collected via Tasks 1-2 instead.

## Fallback when DataForSEO is unavailable

### Amazon data

1. `web_search`: "{product} site:amazon.com" — count results, extract prices from snippets.
2. `web_search`: "{product} amazon best seller rank" — BSR signals.
3. `web_search`: "{product} amazon reviews analysis" — review theme summaries.
4. Browser: visit Amazon category page directly.

### Google Shopping data

1. `web_search`: "{product} price comparison" — comparison site data.
2. Browser: visit Google Shopping.

### Certification and regulatory data

1. `web_search`: "{product} certification requirements US" — compliance articles.
2. `web_search`: "{product} import regulations dropshipping" — import guides.
3. `web_search`: "{product category} FCC FDA CPSC requirements" — regulatory specifics.
4. Agent knowledge: category inference (electronics → FCC, children's → CPSC, etc.).
5. Browser: visit CPSC.gov, FDA.gov for specific lookups (high-risk categories).

When using web search fallbacks, clearly mark `data_sources_used` and `data_gaps` so supervisor can set appropriate confidence levels.

## Guardrails

- Respect `503` (CLI exit 2) when SEO research is unavailable; say so explicitly.
- Summarize reviews; do not dump PII or full review bodies into chat unless needed.
- Mention `cost_usd` from responses only when the owner asks about spend.
