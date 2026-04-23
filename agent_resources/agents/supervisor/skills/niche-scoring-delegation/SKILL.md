---
name: niche-scoring-delegation
description: "Decompose niche analysis into scout Agent Tasks, delegate data collection, then yield. Phase 2 (scoring + report) is handled by niche-scoring-report."
---

# Niche Scoring — Delegation (Supervisor)

**Goal:** Decompose niche evaluation into structured Agent Tasks, delegate data collection to scout, then yield. Supervisor does **not** score or format reports in this phase — that happens in the **`niche-scoring-report`** skill after research completes.

## Canonical dimensions and weights

Use **exactly** these six dimensions and weights — no substitutes (no "audience", "risk", "supplier" as top-level scores).

| # | Dimension | Weight | Short |
|---|-----------|--------|-------|
| 1 | Demand Assessment | 20% | Demand |
| 2 | Competitor Analysis | 15% | Compet |
| 3 | Price Analysis | 15% | Price |
| 4 | Niche Trends | 20% | Trends |
| 5 | Potential Margin | 20% | Margin |
| 6 | Barrier to Entry | 10% | Barrier |

**Formula:**

```
score = Demand×0.20 + Compet×0.15 + Price×0.15
      + Trends×0.20 + Margin×0.20 + Barrier×0.10
```

## Quick — compact card template (use verbatim)

For quick mode, supervisor may use this template directly after receiving scout data, applying scoring rubrics from `niche-scoring-report`.

```
Quick Screening: {niche_name} ({country})
Score: ~{XX} | {label} | Confidence: {level}

 Demand | Compet | Price | Trends | Margin | Barrier
  ~{XX} |  ~{XX} | ~{XX} |  ~{XX} |  ~{XX} |  ~{XX}

Key signals:
+ {positive signal 1}
+ {positive signal 2}
- {negative signal 1}

Quick screening — scores are approximate.
Say "analyze this deeper" for full assessment.
```

## When to use

**Mandatory.** Any request to evaluate, score, rate, or compare product niches:

- "evaluate niche X", "is niche X worth it?", "score this niche"
- "compare these niches", "which niche is better?"
- "assess this niche briefly/superficially" → use quick mode

**Never** answer a niche evaluation request with a free-text opinion. Always run the full skill workflow. This skill supersedes niche-related templates in `product-scout-delegation`.

## Execution checklist

All API calls: `Authorization: Bearer $AGENT_API_KEY`, `Content-Type: application/json`.

Follow these steps **in order**. Do not skip any step.

### STEP 0: Detect effort level

Read the user's message. Map to: `quick` / `standard` / `deep`.
If ambiguous → `standard`.

### STEP 1: Create Team Task

Execute:

```
POST {{api_base_url}}/goals/team-tasks
Body: {"title": "Niche: {niche} ({country}) [{effort}]", "description": "6-dimension niche analysis", "priority": 1, "auto_approve": true}
```

Save `team_task_id`. The task is created in `pending` status (thanks to `auto_approve`).
Then start it: `POST {{api_base_url}}/goals/team-tasks/{team_task_id}/start`

⚠️ VERIFY: Did you call `POST .../goals/team-tasks` and receive a `team_task_id`? If no → STOP. Do it now.

### STEP 2: Create Agent Task(s) and spawn scout

First, determine the **research tier** from agent config capabilities (see **Appendix A**). No test API calls — use the declared capabilities.

- **quick** → 1 Agent Task. Copy the **QUICK** template from **Appendix B.1** verbatim (substitute `{niche}`, `{country}`, `{tier}`, `{task_id}`).
- **standard** → 3 Agent Tasks minimum; copy **Appendix B.2** templates 1–3 verbatim. Add **Appendix B.3** (Task 4) only when that section says to.
- **deep** → 4–5 Agent Tasks. Use **Appendix B.4** (standard tasks + expansions, always Task 4, Task 5 after Task 2 completes).

For **each** task, follow this exact sequence:

1. **Prepare** the template from Appendix B. Substitute `{niche}`, `{country}`, `{tier}` now. Leave `{task_id}` / `{task_N_id}` as-is for now.
2. **Create** the Agent Task:
   ```
   POST {{api_base_url}}/goals/agent-tasks
   Body: {"title": "...", "team_task_id": "<team_task_id>", "assigned_to": "scout", "description": "<prepared template>"}
   ```
3. **Save** the returned `id` from the response — this is the `agent_task_id`.
4. **Replace** `{task_id}` / `{task_N_id}` in the template with the actual `agent_task_id`.
5. **Spawn** scout with the fully substituted template as the task description (`product-scout-delegation` flow).

⚠️ VERIFY: For EACH task — did you (a) POST to create it, (b) get the real agent_task_id, (c) substitute it into the template, (d) spawn scout with the final template? If any step was skipped → STOP. Go back.

### After delegation

You will receive a system digest notification when research completes.
The digest will contain collected data and a reference to the
`niche-scoring-report` skill. Follow those instructions to prepare
the final report.

For quick mode: you may use the compact card template from the top of
this file directly, applying scoring rubrics from `niche-scoring-report`.

## Effort levels (summary)

Default: `standard`.

| Trigger words | Level |
|--------------|-------|
| "quick", "scan", "screening", "brief", "fast check" | `quick` |
| (no qualifier) | `standard` |
| "deep", "thorough", "detailed", "comprehensive", "full", "maximum" | `deep` |

- **Quick:** 1 Agent Task; ~5–8 API calls; no browser; dimension scores rounded to nearest 10; compact card only.
- **Standard:** 3 Agent Tasks (+ conditional Task 4); ~20–30 API calls; browser as fallback; full standard report.
- **Deep:** 4–5 Agent Tasks; ~50–70 API calls; triangulation + mandatory browser verification; comprehensive report.

Include effort in Team Task title for traceability (see STEP 1). Details: confidence caps, precision, progressive disclosure → **Appendix A**.

---

## Appendix A — Effort levels, tiers, confidence (reference)

### Research tiers

From the agent config capabilities block (no probe calls):

```
if research_seo AND research_social -> Tier 1
else if research_trends AND web_search -> Tier 2
     (if research_seo OR research_social: Tier 2+)
else if web_search -> Tier 3
else if browser_access -> Tier 4
else -> cannot perform niche analysis (report to user)
```

Include the detected tier in standard/deep reports (see `niche-scoring-report`).

### Confidence caps by effort × tier

| Effort | Tier 1 max | Tier 2 max | Tier 3 max | Tier 4 max |
|--------|------------|------------|------------|------------|
| `quick` | Medium | Low–Medium | Low | N/A (incompatible) |
| `standard` | High | Medium | Low–Medium | Low |
| `deep` | High | Medium–High | Medium | Low–Medium |

Precision: `quick` → round to 10; `standard` → round to 5; `deep` → exact (before composite).

### Multi-niche progressive disclosure

For multi-niche requests (3+ niches):

1. Default to `quick` for the initial comparison (even without the word "quick").
2. Present the comparison table from quick screening.
3. Ask: "Want me to do a standard or deep analysis on any of these?"
4. Run standard/deep only on the 1–3 niches the user selects.

### Quick + Tier 4 incompatibility

Quick mode needs API or web search. Browser-only (Tier 4) cannot be quick. Warn: *"Browser-only mode requires at least standard effort."*

---

## Appendix B — Agent Task templates (copy verbatim)

Substitute `{niche}`, `{country}`, `{tier}`, `{task_id}` / `{task_N_id}`, and ASIN placeholders where noted.

### B.1 Quick: single combined template

```
agent_task_id: {task_id}

Effort: QUICK — collect minimum viable signals. Budget: ~5-8 API calls total.
Do NOT use browser. Do NOT deep-dive competitors. Single source per data point.

Collect for "{niche}" (target market: {country}), research tier: {tier}:

1. Google Trends interest-over-time (12-month) for "{niche}" — 1 call
2. [If DataForSEO]: keyword-volume for "{niche}" — 1 call
   [Else]: skip, use Trends relative value
3. [If DataForSEO]: amazon-products for "{niche}" — 1 call
   [Else]: web_search "{niche} site:amazon.com" — 1 call
4. [If Supplier API]: product search "{niche}" — 1 call
   [Else]: web_search "{niche} wholesale price" — 1 call
5. web_search "{niche}" — extract competitor count and price range from results — 1 call

Return: single JSON combining all niche-data-collection types (demand+trends,
competition+pricing, supplier+cost fields merged). Include data_sources_used
and data_gaps.
```

### B.2 Standard: templates 1–3

#### Agent Task 1: Demand & Trends Data Collection

```
agent_task_id: {task_1_id}

Effort: STANDARD
Collect demand and trend signals for "{niche}" (target market: {country}).
Research tier: {tier}. Return structured JSON.

1. TREND DATA
   - Google Trends interest-over-time: 12-month and 5-year
   - Google Trends related-queries: top and rising
   [If DataForSEO available]: DataForSEO keyword-trends for precision graph
   [If SociaVault available]: TikTok search engagement, Reddit mention volume

2. SEARCH VOLUME
   [If DataForSEO available]: keyword-volume for seeds "{niche}",
     "{niche_variation_1}", "{niche_variation_2}" — extract monthly volume and CPC
   [Else]: use web_search to find volume estimates
     in SEO articles; query: "{niche} search volume monthly 2026"

3. MARKETPLACE PRESENCE
   [If DataForSEO available]: amazon-products for "{niche}" — count,
     price range, top review counts
   [Fallback]: use web_search: "{niche} site:amazon.com" — count
     results, extract prices from snippets

Return JSON: {trend_direction, growth_rate_12m_pct, current_interest_level,
  search_volume_monthly, search_volume_source, cpc_usd, rising_related_queries[],
  seasonality, peak_months[], amazon_listing_count, amazon_price_range{min,max,median},
  tiktok_engagement, reddit_mentions, data_sources_used[], data_gaps[]}
```

#### Agent Task 2: Competition & Pricing Data Collection

```
agent_task_id: {task_2_id}

Effort: STANDARD
Collect competitor and pricing data for "{niche}" (target market: {country}).
Research tier: {tier}. Return structured JSON.

1. SERP COMPETITION
   [If DataForSEO available]: serp-competitors for "{niche}",
     "buy {niche}" — extract domain count, keyword overlap
   [Else]: web_search "{niche} shop" —
     count independent stores vs major retailers in results

2. MARKETPLACE PRICING
   [If DataForSEO available]: product-search (Google Shopping) — seller count,
     price range, price clustering
   [Else]: web_search "{niche}" with shopping-oriented queries;
     extract prices from snippets

3. AD DENSITY
   [If SociaVault available]: ad-library-search for "{niche}"
     on facebook and google platforms
   [Else]: web_search "{niche} ads" — look for ad transparency
     reports or marketing analyses
   [If browser available]: visit facebook.com/ads/library

4. TOP COMPETITORS (max 5)
   For each: store URL, estimated product count, price range, strengths/weaknesses
   [If browser available]: visit 2-3 stores for deeper analysis

Return JSON: {serp_competitor_count, serp_difficulty, google_shopping_seller_count,
  retail_price_range{min,max,median,currency}, price_spread_ratio, premium_segment_exists,
  active_ad_count{facebook,google}, ad_density,
  top_competitors[{url,product_count,price_range,strength,weakness}],
  data_sources_used[], data_gaps[]}
```

#### Agent Task 3: Supplier & Cost Data Collection

```
agent_task_id: {task_3_id}

Effort: STANDARD
Find supplier options for "{niche}", target market {country}.
Research tier: {tier}. Return structured JSON.

1. SUPPLIER PRODUCT SEARCH
   [If Supplier API available]: search /suppliers/{provider}/products
     for "{niche}", page_size=20
   [Else]: web_search "{niche} wholesale price
     dropshipping" — extract price estimates from supplier review articles
   [Else]: web_search "{niche} site:aliexpress.com" — use
     AliExpress prices as supplier cost proxy
   [If browser available]: visit cjdropshipping.com or aliexpress.com directly

2. TOP 3 CANDIDATES (if Supplier API available)
   For each: load variants, check stock, calculate shipping to {country}
   Use supplier-matching skill workflow steps 1-4

3. SHIPPING ESTIMATE
   [If Supplier API available]: shipping/calculate to {country}
   [Else]: web_search "cj dropshipping shipping time {country}" — extract
     typical shipping times and costs from reviews/guides
   [Fallback]: assume standard dropshipping: $3-7 shipping, 10-20 days

Return JSON: {supplier_available, supplier_source, sku_count,
  supplier_cost_range{min,max,avg,currency},
  best_candidate{product_id,name,cost,variants_count,in_stock,stock_quantity,has_images},
  shipping{method,cost_usd,days_min,days_max,source}, moq,
  data_sources_used[], data_gaps[]}
```

### B.3 Standard: template 4 (conditional)

Create **only** in standard mode if tasks 1–3 are promising **and** tier allows browser. In **deep** mode, always create (see **B.4**).

```
agent_task_id: {task_4_id}

Deep-dive competitor and market analysis for "{niche}".
Browser-based research to fill gaps from prior tasks.

1. Visit 2-3 top competitor stores from Task 2 results. Assess:
   - Store quality (design, trust badges, shipping policy)
   - Product page quality (photos, descriptions, reviews)
   - Price positioning strategy

2. Visit Facebook Ad Library (facebook.com/ads/library):
   - Search "{niche}"
   - Count active ads, note creative formats, identify long-running ads

3. Check certification requirements:
   - Search for "{niche} FCC certification" and "{niche}
     UL certification" — are these required for {country}?
   - Search for "lithium battery shipping restrictions dropshipping"

Return JSON: {competitor_deep_dives[{url,store_quality,product_quality,pricing_strategy}],
  ad_library{active_count,longest_running_days,themes[]},
  certifications_needed[{type,severity,notes}], shipping_restrictions[], notes}
```

### B.4 Deep: expansions + template 5

**Append to each standard task 1–3** the matching expansion block below. **Task 4** is always created in deep mode. **Task 5** runs after Task 2 completes (needs ASINs).

**Task 1 expansion:**

```
Effort: DEEP — use ALL available sources for cross-referencing.
Test 5-10 keyword variations (not just the seed). Query both 12-month AND 5-year
trends. Use DataForSEO keyword-trends AND Google Trends for cross-reference.
Check SociaVault: TikTok search + TikTok trending + Reddit search + YouTube Shorts.
Run DataForSEO content-sentiment for media polarity. Report all values from all
sources even if they overlap — supervisor will triangulate.
```

**Task 2 expansion:**

```
Effort: DEEP — triangulate from multiple sources.
Check SERP competitors + Google Shopping + Amazon via DataForSEO. ALSO run web_search
for Shopify store count. ALSO check SociaVault ad library for both Facebook and Google.
Extract full price dataset from all marketplace sources. Visit 5-8 competitor stores
via browser for qualitative assessment. Check Facebook Ad Library directly via browser
for ad longevity data.
```

**Task 3 expansion:**

```
Effort: DEEP — evaluate top 5-10 supplier candidates (not just 3). Compare multiple
shipping methods. Check stock depth across variants. Calculate shipping to multiple
regions if applicable.
```

#### Agent Task 5 (deep only): Customer Voice Analysis

**Dependency:** requires ASINs from Task 2. Create only after Task 2 completes (tasks 1, 3, 4 may run in parallel with others as appropriate).

```
agent_task_id: {task_5_id}

Effort: DEEP
Customer voice and demand validation for "{niche}" (target market: {country}).

Top ASINs from competition research: {asin_1}, {asin_2}, {asin_3}
(extracted from Task 2 outcome)

1. [If DataForSEO]: amazon-reviews for the ASINs above —
   recurring praise and complaints
2. [If DataForSEO]: people-also-ask for "{niche}" — buyer questions
3. [If SociaVault]: TikTok Shop reviews for top products
4. [If SociaVault]: Reddit subreddit search in r/dropshipping, r/ecommerce,
   r/Entrepreneur for "{niche}"
5. web_search: "{niche} complaints" and "{niche} problems"

Return JSON: {customer_praise_themes[], customer_complaint_themes[],
  positioning_angles[], paa_questions[], tiktok_shop_review_themes[],
  reddit_discussion_themes[], data_sources_used[], data_gaps[]}
```
