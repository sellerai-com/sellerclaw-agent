---
name: marketing-delegation
description: "Delegate advertising and paid acquisition tasks to the Marketing Manager subagent."
---

# Marketing Manager delegation

## Purpose

The Marketing Manager (`marketing`) handles paid advertising campaigns on Facebook
and Google Ads: campaign creation, optimization, budget management, A/B testing,
audience management, and performance reporting.

## Communication contract

**Task format:** always specify the platform (Facebook/Google) and objective.
Campaign creation and activation always require owner approval — the agent returns
a plan, not a live campaign.

**Response format:** structured result with outcome, data, and errors.
May return a `download_url` for file deliveries (CSV reports, etc.).

## Task templates

**Fetch campaign performance:**
> Platform: {facebook|google}. Fetch metrics for all active campaigns. Level: adset. Period: {date_range}. Include spend, conversions, CPA, ROAS, CTR, frequency. Also fetch the preceding period of equal length for comparison.

**Create a campaign for a product:**
> Platform: Facebook. Create a campaign for product: {product_name}. Store URL: {url}. Price: ${price}. Target audience: {description}. Daily budget: ${budget}. Objective: CONVERSIONS. Return a plan with campaign structure, targeting, and creative suggestions. Do NOT activate.

**Optimize active campaigns:**
> Platform: Facebook. Review all active ad sets. Apply optimization rules: kill CPA > 2× target, scale ROAS > target with stable 3-day performance, flag frequency > 3.0. Return action list with recommendations.

**Pause underperformers:**
> Platform: {facebook|google}. Pause ad sets: {adset_ids}. Confirm execution.

**Scale winning ad sets:**
> Platform: Facebook. Increase daily budget by 20% for ad sets: {adset_ids}. Current budgets: {budget_list}. Confirm new budgets and execution.

**A/B test creative:**
> Platform: Facebook. Duplicate ad set {adset_id}. Replace creative with: title "{title}", body "{body}", image from {image_url}. Split budget 50/50 between original and variant. Name variant: "{test_name}".

**Create lookalike audience:**
> Platform: Facebook. Create 1% lookalike audience in US based on source audience {audience_id}. Name: "{name}".

**Weekly performance digest:**
> All platforms. Generate weekly performance digest for period {date_range}. Include: total spend, conversions, revenue, blended ROAS, top/bottom performers, recommendations.

## Constraints

- The subagent does not communicate with the user directly.
- The subagent cannot create sessions, send messages, or manage cron jobs.
- All ad platform API calls go through the system API, not directly to Facebook/Google.
- For monitoring delegated task progress, use the `delegation-monitoring` skill.
