---
name: niche-scoring-report
description: "Score scout data using 6-dimension rubrics, format the user-facing niche report, complete the Team Task."
---

# Niche Scoring — Report (Supervisor)

Use **after** `niche-scoring-delegation` workflow: all Agent Tasks are complete or failed; you have scout JSON outcomes.

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

⚠️ VERIFY: Are you using EXACTLY these 6 names and weights? If you wrote "audience", "risk", "supplier" or anything else → STOP. Those are wrong. Use only the 6 above.

---

## Scoring rubrics

Apply these rubrics to the scout's raw JSON data. Each dimension uses a 0–100 scale.

### Dimension 1: Demand Assessment (20%)

**Input:** `search_volume_monthly`, `current_interest_level`, `amazon_listing_count`, `tiktok_engagement`

| Score | Criteria |
|-------|----------|
| 80-100 | Monthly volume >10K; 100+ Amazon listings with reviews; social signals present |
| 60-79 | Volume 3K-10K; 20-100 Amazon listings; some social presence |
| 40-59 | Volume 1K-3K; 5-20 Amazon listings; minimal social |
| 20-39 | Volume <1K; few marketplace listings |
| 0-19 | Near-zero volume; no meaningful marketplace presence |

**When volume is unavailable (no DataForSEO):** Use Google Trends relative interest.
Above 50 average on 0-100 scale → moderate demand. Cross-reference with Amazon listing
count from web search. Reduce confidence by one level.

### Dimension 2: Competitor Analysis (15%) — inverted: less competition = higher score

**Input:** `serp_competitor_count`, `google_shopping_seller_count`, `ad_density`, `top_competitors`

| Score | Criteria |
|-------|----------|
| 80-100 | <5 active competitors; no dominant brands; few/no active ads |
| 60-79 | 5-15 competitors; no single brand dominant; moderate ads |
| 40-59 | 15-30 competitors; some established brands; regular ads |
| 20-39 | 30+ competitors; 2-3 dominant brands; heavy ad spending |
| 0-19 | Saturated; major brand dominance; extreme ad density |

### Dimension 3: Price Analysis (15%)

**Input:** `retail_price_range`, `price_spread_ratio`, `premium_segment_exists`
**Cross-reference:** `supplier_cost_range`, `shipping.cost_usd`

**Derived metric:** `price_floor_vs_cost` = lowest retail price / (avg supplier cost + shipping). If <1.3, race-to-bottom risk.

| Score | Criteria |
|-------|----------|
| 80-100 | Spread ratio >3x; premium segment exists; floor well above cost |
| 60-79 | Spread 2-3x; some premium products; floor above cost with room |
| 40-59 | Spread 1.5-2x; commodity pricing; thin room above cost |
| 20-39 | Spread <1.5x; race-to-bottom signals |
| 0-19 | Extreme compression; floor near or below supplier+shipping |

### Dimension 4: Niche Trends (20%)

**Input:** `trend_direction`, `growth_rate_12m_pct`, `rising_related_queries`, `seasonality`, `tiktok_engagement`, `reddit_mentions`

| Score | Criteria |
|-------|----------|
| 80-100 | >30% YoY growth; multiple rising queries; social momentum; early-stage pattern |
| 60-79 | 15-30% growth; some rising queries; positive direction |
| 40-59 | Stable (+/-15%); mature niche; no clear signal |
| 20-39 | Declining 15-30%; rising queries drying up |
| 0-19 | Rapid decline >30%; fad collapse; no rising queries |

**Seasonal adjustment:** Score year-over-year comparison of peak periods, not
within-year fluctuation. Flag seasonality but don't penalize.

### Dimension 5: Potential Margin (20%)

**Input:** supplier cost + shipping (Task 3), retail price median (Task 2), CPC (Task 1)

**Unit economics formula:**

```
retail_price       = Task 2: retail_price_range.median
supplier_cost      = Task 3: best_candidate.cost (or supplier_cost_range.avg)
shipping_cost      = Task 3: shipping.cost_usd
platform_fee       = retail_price * platform_rate + fixed_fee
estimated_cpa      = cpc_usd / 0.02   (2% conversion rate for cold traffic)
gross_margin       = retail_price - supplier_cost - shipping_cost
net_margin_organic = gross_margin - platform_fee
net_margin_paid    = net_margin_organic - estimated_cpa
margin_pct         = net_margin_organic / retail_price * 100
```

**Platform fees:** Shopify 2.9%+$0.30 | eBay 13.25% | Amazon FBM 15% | TikTok Shop 5%+$0.30

| Score | Criteria (organic net margin %) |
|-------|----------|
| 80-100 | Net margin >40% |
| 60-79 | 25-40% |
| 40-59 | 15-25% |
| 20-39 | 5-15% |
| 0-19 | <5% or negative |

**When CPC unavailable:** Score margin without CPA, note "excludes ad cost", cap confidence at Medium.

**When supplier cost is estimated (no API):** Use best available estimate, note source, reduce confidence.

### Dimension 6: Barrier to Entry (10%) — inverted: lower barrier = higher score

**Input:** supplier SKU count and shipping days (Task 3), certification data (Task 4 or knowledge), MOQ (Task 3)

**Sub-signals** (high→low barrier): SKUs 0-2→10+; shipping >25d/$10→7-14d/<$5; certs FDA/CPSC→none; capital >$5K→<$500; MOQ >100→none; brand registry required→not needed; IP active patents→generic.

| Score | Criteria |
|-------|----------|
| 80-100 | 10+ SKUs; fast cheap shipping; no certs; low MOQ; <$500 start |
| 60-79 | 5-10 SKUs; moderate shipping; minor compliance; $500-2K start |
| 40-59 | 3-5 SKUs; slow/expensive shipping; some certs (FCC); $2K-5K start |
| 20-39 | 1-2 SKUs; significant certs (FDA/CPSC); brand required; $5K-15K start |
| 0-19 | No reliable suppliers; regulated category; heavy IP; >$15K start |

---

## Confidence and interpretation

### Confidence assignment

| Level | Criteria |
|-------|----------|
| High | 5-6 dimensions scored from primary sources (Tier 1 tools) |
| Medium | 3-4 dimensions from primary sources, others from fallbacks |
| Low | Most dimensions from web search / browser / estimates |

### Interpretation bands

| Score | Label | Guidance |
|-------|-------|----------|
| 75-100 | Strong opportunity | High confidence entry; proceed to product selection |
| 55-74 | Moderate opportunity | Viable with solid execution and differentiation |
| 35-54 | Proceed with caution | Significant risks; needs clear competitive advantage |
| 0-34 | Avoid | Unfavorable conditions; look for better niches |

### Score precision by effort

| Effort | Precision |
|--------|-----------|
| `quick` | Round to nearest 10 |
| `standard` | Round to nearest 5 |
| `deep` | Exact (before composite) |

---

## Output templates

### Quick — compact card (use verbatim)

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

### Standard — full report

```
Niche Score: {niche_name}
Target market: {country} | Research tier: {1-4} | Confidence: {High/Medium/Low}
Composite Score: {0-100} — {label}

 Dimension           | Score | Key evidence
---------------------+-------+----------------------------------
 Demand Assessment    | {XX}  | {volume}K monthly searches; {N} Amazon listings
 Competitor Analysis  | {XX}  | {N} SERP competitors; ad density: {level}
 Price Analysis       | {XX}  | ${min}-${max} retail; spread ratio {X.X}x
 Niche Trends         | {XX}  | {direction} ({+/-X}% YoY); {N} rising queries
 Potential Margin     | {XX}  | Net margin: {X}% organic / {X}% paid
 Barrier to Entry     | {XX}  | {N} suppliers; {cert notes}; ~${X} to start

Unit economics (best candidate):
  Supplier: ${X.XX} | Shipping: ${X.XX} | Platform fee: ${X.XX}
  Est. CPA: ${X.XX} | Retail: ${X.XX} | Net margin: {X}% organic

Risks: [...]
Opportunities: [...]
Data limitations: [list of unavailable sources, if any]

Recommendation: {actionable next step}
```

### Deep — comprehensive report

Standard report (above) + these additional sections:

1. **Data Triangulation Notes** — per-metric: source A vs source B, convergent/divergent
2. **Customer Voice** — praise themes, complaint themes, positioning angles, PAA questions
3. **Competitor Deep-Dives** — 5–8 profiles with qualitative assessment
4. **Certification & Compliance Detail** — type, required/recommended, cost, timeline
5. **Extended Unit Economics** — multi-platform scenarios (Shopify, Amazon, TikTok Shop)
6. **Recommended Strategy** — channel, positioning, pricing, investment, validation

### Multi-niche comparison

```
Niche Comparison: {N} niches

 # | Niche              | Score | Dem | Comp | Price | Trend | Margin | Barrier | Conf
---+--------------------+-------+-----+------+-------+-------+--------+---------+------
 1 | {name}             | {XX}  | {XX}| {XX} | {XX}  | {XX}  | {XX}   | {XX}    | High
 2 | {name}             | {XX}  | {XX}| {XX} | {XX}  | {XX}  | {XX}   | {XX}    | Med

Top recommendation: {niche} — {rationale}
```

---

## Effective research tier

The research tier declared during delegation (Tier 1–4 from config capabilities)
may differ from what actually produced usable data. Determine the **effective tier**
from scout's `data_sources_used[]` and `data_gaps[]` in the returned JSON:

| Effective Tier | Condition |
|----------------|-----------|
| Tier 1 | Scout used DataForSEO + SociaVault successfully |
| Tier 2 | Scout used Google Trends + web_search; DataForSEO or SociaVault partially failed |
| Tier 3 | Scout relied primarily on web_search (most API sources failed) |
| Tier 4 | Scout used browser for most data points |

If effective tier < declared tier, note the discrepancy in the "Data limitations"
section of the report. Adjust confidence caps to match the effective tier, not the
declared one.

Example: declared Tier 2, but DataForSEO returned empty results for all queries →
effective Tier 3 → confidence cap is Low–Medium (not Medium).

## Handling partial data

If a sub-task fails, score available dimensions, redistribute weight proportionally,
mark missing as "N/A". Always show data sources used vs unavailable. Never fabricate
scores — exclude unevaluable dimensions and note the omission.

## Multi-niche workflow

When analyzing multiple niches:

1. Create one Team Task for the comparison.
2. Create Agent Tasks 1–3 **per niche** (can run concurrently).
3. Score each niche independently.
4. Produce both individual breakdowns and the comparison table.
5. Cap at 8 niches per comparison to keep analysis manageable.

## Guardrails

- Do not re-score scout's raw data — use it as-is and apply rubrics.
- If all sub-tasks fail, report the failure clearly instead of guessing.
- Always include data limitations section when operating below Tier 1.
- **Auto-escalation:** If quick screening score is borderline (35-75), suggest
  upgrading to standard. Do not auto-upgrade without asking.
- **Deep mode budget check:** Before starting deep analysis, warn if estimated cost
  (50-70 queries) would consume >10% of remaining period budget.

---

## After report: complete Team Task

```
POST {{api_base_url}}/goals/team-tasks/{team_task_id}/request-review
Body: {"outcome": "Niche Score: {niche}: {score}/100 ({label})"}
```
