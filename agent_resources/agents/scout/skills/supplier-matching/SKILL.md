---
name: supplier-matching
description: "Find and rank supplier candidates for a shortlisted product on price, stock, shipping, and quality through a connected supplier provider via the `sellerclaw` CLI. Use when you have a product or hero keyword and need real supplier options. Research only — not for purchase, fulfillment, or catalog DB writes."
---

# Supplier Matching Skill

## Goal

After niche selection and product discovery, find the best supplier options for each shortlisted product. Compare on price, shipping, stock reliability, and product quality.

## Conventions

- All supplier calls use `sellerclaw suppliers <subcommand> <provider> …`. JSON on stdout; structured errors on stderr (exit 1=user/api, 2=server/network, 3=auth).
- **Choose `<provider>`** by running `sellerclaw agent-context list-integrations` and picking an entry whose `kind` starts with `supplier_` (excluding `supplier_any`) with at least one active connection. The provider id is the `kind` suffix (e.g. `supplier_cj` → `provider=cj`). If no such entry is returned, you cannot call supplier catalog endpoints — return a partial result and say supplier API is unavailable.
- For supplier catalog CLI shapes and per-provider quirks, read the supplier subagent skill **`product-search`** (provider-neutral flow + per-provider notes, e.g. CJ).

## Workflow

**Progress checkpoints:** if the task includes an `agent_task_id`, report progress after Steps 2 and 4 via `sellerclaw agent-goals add-progress-note <task_id> -b '{"message":"…"}'`. Include concrete data (product names, variant IDs, prices, stock status) so results survive session timeouts.

### Step 0 — Resolve provider

Run `sellerclaw agent-context list-integrations` and pick one provider id (the suffix after `supplier_`) from an entry with an active connection — e.g. `cj` from `supplier_cj`. If multiple are returned, prefer the one that best matches the product source requested by the supervisor; otherwise use the first listed.

### Step 1 — Search for product matches

For each shortlisted product (from niche scoring or supervisor request):

```bash
sellerclaw suppliers search-products $PROVIDER --query "<product_keywords>" --page-size 10
```

Select the top 3 candidates based on: name relevance, image quality (has images), and source price.

### Step 2 — Load variants and check stock

For each candidate product:

```bash
# Get variants
sellerclaw suppliers get-variants $PROVIDER <product_id>

# Check stock for the primary variant
sellerclaw suppliers check-stock $PROVIDER <variant_id>
```

Skip products where primary variant is out of stock.

**Checkpoint after Step 2:** report found candidates per product (name, variant ID, price, stock status).

### Step 3 — Calculate shipping

For each in-stock candidate:

```bash
sellerclaw suppliers calculate-shipping $PROVIDER -b '{
  "items": [{"variant_id": "<vid>", "quantity": 1, "shipping_method": "CJPacket"}],
  "shipping_address": {
    "country_code": "<target_country>",
    "province": "CA", "city": "Los Angeles", "zip_code": "90001",
    "address_line": "Test", "full_name": "Test", "phone": "+10000000000"
  }
}'
```

Use a representative address in the target country for estimation. Body schema: `sellerclaw describe suppliers_calculate_shipping_suppliers__provider__shipping_calculate_post`.

For **CJ** in the US, preferred shipping methods: `CJPacket` > `ePacket` > `USPS`. For other providers, follow their supported method names from the supplier agent skill or CLI errors.

### Step 4 — Score and rank

Score each supplier candidate:

| Factor | Weight | Scoring |
|---|---|---|
| Total cost (source + ship) | 35% | Lower = better; normalize to 0–100 vs candidates |
| Shipping speed | 25% | 7–12d = 100, 12–18d = 70, 18–25d = 40, 25d+ = 10 |
| Stock availability | 20% | In stock with quantity > 50 = 100, > 10 = 70, low = 30 |
| Product quality signals | 20% | Has images + variants + description = 100, partial = 50, minimal = 20 |

### Step 5 — Present comparison

```
Product: "{product_name}"
Target market: {country}

 # │ Supplier │ Cost   │ Ship   │ Total  │ Est Margin │ Ship Days │ Stock │ Score
───┼──────────┼────────┼────────┼────────┼────────────┼───────────┼───────┼──────
 1 │ A    │ $4.20  │ $2.80  │ $7.00  │ ~60%       │ 8–12d    │ ✓ 200 │ 88
 2 │ B    │ $3.90  │ $3.50  │ $7.40  │ ~58%       │ 12–18d   │ ✓ 45  │ 72
 3 │ C    │ $5.10  │ $2.20  │ $7.30  │ ~59%       │ 10–14d   │ ✓ 120 │ 79

Recommended: A — best balance of cost, shipping speed, and stock depth.
```

Estimated margin: if no competitor data, estimate sell price as `total_cost × 2.5`.

## Efficiency rules

- **One search per product keyword.** Do not run 5 variations of the same query.
- **Top 3 candidates max** per product. Do not evaluate every search result.
- **Stock + shipping check only for candidates**, not for every search result.
- **Budget CLI calls**: for N products, expect ~4N calls (search + variants + stock + shipping). If exceeding 6N, stop and report partial results.

## Guardrails

- Retry failed CLI calls at most twice.
- Do not confirm or create supplier orders — this skill is for research only. Purchasing is handled by the `supplier` agent via the catalog management workflow.
- Always include shipping cost in total cost calculations — never report source price alone.
- If a product has no images, flag it as a risk but do not auto-exclude (owner may accept).
- Note when stock quantity is null (some suppliers omit it) — flag as "stock unverified."

## Scope limits by effort

Read the effort level from the Agent Task instructions (`Effort: QUICK/STANDARD/DEEP`). If not stated, use Standard.

| Limit | Quick | Standard | Deep |
|-------|-------|----------|------|
| Supplier candidates evaluated | 1 (top result) | 3 (top 3 with stock/ship) | 5-10 (full comparison) |
| Shipping methods compared | 1 (cheapest) | 1-2 (cheapest + fastest) | 3+ (all available) |
| Variant depth | Skip | Primary variant only | All variants with stock check |
| Browser supplier visits | 0 | 0 (fallback only) | 1-2 (for verification) |

## Fallback when supplier CLI is unavailable

If `sellerclaw agent-context list-integrations` returns no active `supplier_*` connection (other than `supplier_any`) or supplier CLI calls return errors:

1. `web_search`: "{product} wholesale price dropshipping" — supplier cost estimates.
2. `web_search`: "{product} site:aliexpress.com" — AliExpress as cost proxy.
3. `web_search`: "{product} site:cjdropshipping.com" — CJ indexed product pages.
4. `web_search`: "dropshipping {product category} shipping time cost US" — shipping estimates.
5. Browser: visit cjdropshipping.com or aliexpress.com directly for product search.
6. Last resort: reverse-calculate supplier cost from retail median / 2.5-3x.

Mark `supplier_source` in return data accordingly:
- `"aliexpress_web"` if prices came from AliExpress search results
- `"wholesale_estimate"` if from wholesale/dropshipping articles
- `"unavailable"` if no data could be collected

When using web estimates, note in `data_gaps` that supplier data confidence is Low.
