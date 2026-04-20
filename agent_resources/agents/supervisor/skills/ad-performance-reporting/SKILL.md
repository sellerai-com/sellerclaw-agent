---
name: ad-performance-reporting
description: Build ad campaign performance reports, identify winners and losers, and recommend optimization actions.
---

# Ad Performance Reporting Skill

## Goal
Generate clear, actionable reports on advertising campaign performance. Surface winners to scale, losers to kill, and trends to act on.

## Triggers
- Owner command to check ad performance / ROAS / ad spend.
- System-automated daily review (push notification at 9:00 UTC).
- Budget reallocation request.
- Anomaly: sudden CPA spike, ROAS drop, or spend anomaly.

## Efficiency Rules (MANDATORY)

- **Report only what matters.** Top 3 winners, bottom 3 losers. Do not dump every ad set.
- **Include date range and attribution window** in every report.
- **Compare periods.** Always show trend: current vs previous (week-over-week or day-over-day).
- **One recommendation per finding.** Do not brainstorm — give the best action.

## Workflow: Performance Review

### 1. Collect Active Campaign Data
Delegate to `marketing`:
> Fetch metrics for all active campaigns. Level: adset. Period: last 7 days. Include: spend, conversions, CPA, ROAS, CTR, frequency. Also fetch previous 7 days for comparison.

### 2. Analyze and Classify

Classify each ad set into one of four buckets:

| Bucket | Criteria | Action |
|---|---|---|
| 🟢 **Scale** | ROAS > target × 1.2, CPA < target, spend > $50, 3+ days | Increase budget 20% |
| 🟡 **Hold** | ROAS near target (±20%), stable metrics | Monitor, no changes |
| 🔴 **Kill** | CPA > target × 2 AND spend > $20, or ROAS < 0.5 | Pause immediately |
| ⚠️ **Fatigue** | Frequency > 3.0, CTR declining 3+ days | Refresh creative or audience |

Default target: CPA < $15, ROAS > 2.0 (adjust per store economics).

### 3. Present Report

```
ℹ️ INFO — Ad Performance Review

Period: Mar 11–18 vs Mar 4–11
Platform: Facebook | Total spend: $1,245 (↑12%)

🟢 SCALE (recommend +20% budget):
1. Lookalike 1% US — CPA $11.20 (↓8%), ROAS 3.1x, $320 spent
2. Interest: Pet Owners — CPA $13.50 (↓3%), ROAS 2.6x, $180 spent

🔴 KILL (recommend pause):
1. Broad 18-65 — CPA $42.00 (↑35%), ROAS 0.6x, $210 spent
2. Interest: Outdoors — CPA $28.50, ROAS 0.9x, $95 spent

⚠️ FATIGUE (recommend creative refresh):
1. Retargeting 7d — Freq 4.2 (was 2.8), CTR 0.9% (was 1.6%)

Summary: $1,245 spent → 72 conversions → $13,680 revenue → blended ROAS 2.2x
Recommendation: pause 2 losers (save ~$305/week), scale 2 winners.

approve all / approve scale only / modify?
```

### 4. Execute Approved Actions
On owner approval, delegate to `marketing`:
> Execute the following changes:
> - Pause ad set {id_1}, {id_2}
> - Increase budget for ad set {id_3} from $25 to $30
> - Increase budget for ad set {id_4} from $20 to $24

## Workflow: Weekly Digest

Generate once per week (automated or on request).

```
ℹ️ INFO — Weekly Ad Digest

Week: Mar 11–18 | Platforms: Facebook + Google

            | This week | Last week | Change
────────────┼───────────┼───────────┼────────
Spend       | $1,845    | $1,620    | ↑14%
Conversions | 108       | 94        | ↑15%
Revenue     | $19,440   | $16,920   | ↑15%
Blended CPA | $17.08    | $17.23    | ↓1%
Blended ROAS| 2.1x      | 2.0x      | ↑5%

Top performer: Lookalike 1% US (ROAS 3.1x)
Worst performer: Broad 18-65 (ROAS 0.6x) — paused on Mar 15

Recommendations:
1. Test 2% lookalike to expand winning audience.
2. Refresh retargeting creative (frequency 4.2).
3. Consider Google Shopping — Merchant Center has 24 products ready.
```

## Guardrails
- Never auto-execute ad changes without owner approval.
- Always report actual numbers, not projections (label estimates clearly).
- If data is partial (API errors, attribution delays), note the gap.
- Default attribution: 7-day click for Facebook, 30-day for Google.
- Include platform source on every metric to avoid confusion.
