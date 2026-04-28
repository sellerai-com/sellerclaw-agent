---
name: campaign-playbook
description: "Provider-agnostic ad-campaign playbook: standard procedures for creating a new campaign, the periodic optimization cycle (kill / scale / fatigue / saturation rules), A/B tests, budget scaling cadence, and emergency rules (CPA blow-up, weekly-spend cap, token expiry). Use whenever the task is to plan a new campaign, run a routine optimization pass, set up an A/B variant, scale spend, or react to a budget/account emergency — pair with `facebook-ads-api` or `google-ads-api` for the actual API calls, and `product-data-api` for catalog inputs."
---

# Campaign playbook

## Creating a new campaign

1. `GET /products/{id}` → name, price, images, stock, variants.
2. Structure: campaign (objective, budget, name) → ad set(s) (targeting, bid, placement, schedule) → ad(s) (creative, copy, CTA, URL).
3. Return plan to supervisor (reach, budget, targeting); **on approval only** create via API and return ids.

Defaults: objective `CONVERSIONS` (sales) / `TRAFFIC` (no pixel); placement `automatic`; bid `lowest_cost` (new) / `cost_cap` (scaling).

## Optimization cycle

1. `GET /ads/{provider}/campaigns?status=ACTIVE` + metrics.
2. Per ad set: kill (`target_cpa`, `min_spend_before_kill`); scale if ROAS/CPA/spend OK 3+ days (+20% max); fatigue (freq >3, CTR down 3d); saturation (CPM +20% WoW).
3. Before scale: `GET /ads/{provider}/action-log?entity_id={adset_id}&days=14`; skip scale if budget changed within 3 days.
4. Return action plan; execute on approval.

## A/B testing

Duplicate winner (one variable: creative / copy / audience), 50/50 budget, 3–7d, ~100 conv/variant, pick lower CPA or higher ROAS, pause loser.

## Scaling playbook

Days 1–3 observe; d4 +20% if ROAS ok; d7 +20%; d10 +20% or duplicate audience; d14+ plateau. Max +20%/day; check `action-log` each step.

## Emergency rules

These override the optimization cycle and execute immediately:

1. **Emergency pause**: if all ad sets in a campaign have CPA > `emergency_cpa_multiplier` × `target_cpa` for 2+ consecutive days, pause the campaign and notify supervisor.
2. **Budget cap**: if weekly spend approaches `max_weekly_ad_spend` (>90%), stop scaling and notify supervisor. If exceeded, pause lowest-performing ad sets to stay within cap.
3. **Token expiry**: if ad API returns auth error, mark account as `TOKEN_EXPIRED` and notify supervisor immediately.
