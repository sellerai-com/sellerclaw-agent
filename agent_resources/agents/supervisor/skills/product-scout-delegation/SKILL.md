---
name: product-scout-delegation
description: "Delegate product research, competitive intelligence, and supplier discovery tasks to the Product Scout subagent. For niche evaluation/scoring, use niche-scoring-delegation (+ niche-scoring-report)."
---

# Product Scout delegation

## Purpose

The Product Scout (`scout`) researches product opportunities and niches for
dropshipping stores: trend analysis, competitive intelligence,
and supplier matching. It helps owners decide **what to sell** before the catalog
management workflow handles **how to list and sell it**.

> **Niche evaluation routing:** For any request to evaluate, score, or compare
> niches, use **`niche-scoring-delegation`** (and **`niche-scoring-report`** for the
> final write-up). They orchestrate the full
> 6-dimension scoring workflow and use this skill's delegation mechanics
> (AgentTask + spawn) for scout data collection.

## Communication contract

**Task format:** specify the research objective (product research vs competitive intelligence),
target market, and any constraints (budget, categories, exclusions).

**Response format:** structured data with product shortlists, competitor snapshots,
and confidence levels. May return a `download_url` for
detailed CSV reports.

## Delegation workflow (mandatory for heavy research)

Scout research (trends, CJ catalog, variants, shipping, browser) is a heavy multi-step
pipeline that routinely takes 5–10 minutes. **Use the task framework** to make progress
recoverable and visible. If the Goals API is unavailable (task framework disabled,
endpoints return 404), fall back to **sessions-only monitoring** via the
`delegation-monitoring` skill — skip Steps 1/3/4 below and use `sessions_spawn` directly.

### Step 1 — Create an AgentTask

Before spawning scout, create an agent task so progress is persisted in the DB:

```bash
curl -s -X POST "{{api_base_url}}/goals/agent-tasks" \
  -H "Authorization: Bearer $AGENT_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"title": "Research niches for US dropshipping store", "description": "...", "assigned_to": "scout"}'
```

Save the returned `task_id`.

### Step 2 — Spawn scout with `task_id` in context

Pass `task_id` in the task description so scout can report progress:

```
sessions_spawn(agentId="scout", task="""
  agent_task_id: {task_id}
  ...task description...
""")
```

- **runTimeoutSeconds:** omit (gateway default **600**) or set **≥ 600**.
  Do **not** pass **300** — it causes timeouts mid-research.

### Step 3 — Monitor via delegation-monitoring skill + Goals API

Use **both** data sources to understand scout status:

- **Session tools** (`sessions_history`, `sessions_list`) — real-time transcript.
- **Goals API** (`GET /goals/events/agent_task/{task_id}` — i.e.
  `/goals/events/{target_kind}/{target_id}` with `target_kind=agent_task`) — persisted
  progress checkpoints that survive session timeouts and restarts.

### Step 4 — Handle timeout / failure

If scout times out or fails:

1. Read progress from Goals API: `GET /goals/events/agent_task/{task_id}`
   (endpoint: `/goals/events/{target_kind}/{target_id}`).
2. Extract what scout already completed (e.g. "trends collected for 5 niches,
   supplier matching pending").
3. Re-spawn scout with a **narrowed scope** and the partial results:
   ```
   sessions_spawn(agentId="scout", task="""
     agent_task_id: {task_id}
     CONTINUATION — previous run timed out.
     Already completed: {summary_from_progress_notes}
     Remaining work: {what_is_left}
   """)
   ```
4. Do **not** start from scratch — the progress notes contain recoverable data.

## Task templates

**Niche evaluation / scoring / comparison:**

> Use **`niche-scoring-delegation`** for these tasks. It handles effort detection,
> tier-aware task decomposition, 6-dimension scoring, and structured output.
> The delegation mechanics (Steps 1-4 above) still apply — niche-scoring-delegation
> uses them for spawning scout sub-tasks.

**Find products in a niche:**

> agent_task_id: {task_id}
>
> Find 5–10 product candidates in niche "{niche}". Target market: {country}.
> Price range: ${min}–${max} (supplier cost). Check trends, find suppliers on CJ,
> estimate margins. Return shortlist sorted by opportunity score.

**Find products for an existing store:**

> agent_task_id: {task_id}
>
> Find new product candidates for store "{store_name}" (niche: {niche}).
> Current catalog has {N} products. Avoid overlap with existing categories: {categories}.
> Target market: {country}. Return 5–10 candidates with supplier data.

**Analyze competitors in a niche:**

> agent_task_id: {task_id}
>
> Analyze top competitors in niche "{niche}". Find 3–5 competitor stores.
> For each: product count, price range, active ads, strengths/weaknesses.
> Identify market gaps and positioning opportunities.

**Validate a product idea:**

> agent_task_id: {task_id}
>
> Validate product idea: "{product_description}". Check Google Trends for demand,
> find suppliers on CJ with pricing, check competitor pricing.
> Return: demand trend, estimated margin, competition level, confidence assessment.

**Trending products discovery:**

> agent_task_id: {task_id}
>
> Find currently trending product categories on Google Trends for {country}.
> Cross-reference with CJ availability. Return top 5 trending categories with
> product examples and supplier pricing.

## Workflow integration

Scout output → supervisor presents shortlist → owner approves → supervisor delegates to `supplier` (sourcing via catalog-management) → then to `shopify`/`ebay` for listing creation.

## Constraints

- Scout does not communicate with the user, create products/listings, or manage cron jobs.
- All API calls go through the system API only.
- Browser-based research requires browser access to be enabled.
- For monitoring delegated task progress, use the `delegation-monitoring` skill.
