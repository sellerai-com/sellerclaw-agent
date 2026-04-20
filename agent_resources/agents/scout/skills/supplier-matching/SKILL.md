---
name: supplier-matching
description: Find and compare suppliers for shortlisted products using connected supplier APIs via sellerclaw-api.
---

# Supplier Matching Skill

## Goal

After niche selection and product discovery, find the best supplier options for each
shortlisted product. Compare on price, shipping, stock reliability, and product quality.

## Base URL and authentication

- Base URL: `{{api_base_url}}`
- Auth header: `Authorization: Bearer $AGENT_API_KEY`
- Do not print token values in logs or messages.

## Conventions

- Use `exec curl` for HTTP requests.
- All supplier endpoints follow the pattern: `/suppliers/{provider}/{endpoint}`.
- **Choose `{provider}`** from the Product Scout core section **Supplier API providers** (`{{available_supplier_providers}}`). Example: if the bundle lists `cj`, use `provider=cj`. If the value is `(none)`, you cannot call supplier catalog endpoints—return a partial result and say supplier API is unavailable.
- For **CJ** field-level schemas and quirks, read skill **`cj-dropshipping`**.

## Workflow

**Progress checkpoints:** if the task includes an `agent_task_id`, report progress
after Steps 2 and 4 via `POST /goals/agent-tasks/{agent_task_id}/progress` with a
JSON body `{"message": "..."}`. Include concrete data (product names, variant IDs,
prices, stock status) so results survive session timeouts.

### Step 0 — Resolve provider

Set `provider` to a single id from `{{available_supplier_providers}}` (e.g. `cj`). If multiple ids are listed, prefer the one that best matches the product source requested by the supervisor; otherwise use the first listed.

### Step 1 — Search for product matches

For each shortlisted product (from niche scoring or supervisor request):

```bash
curl -s "{{api_base_url}}/suppliers/${provider}/products?query={product_keywords}&page_size=10" \
  -H "Authorization: Bearer $AGENT_API_KEY"
```

Select the top 3 candidates based on: name relevance, image quality (has images),
and source price.

### Step 2 — Load variants and check stock

For each candidate product:

```bash
# Get variants
curl -s "{{api_base_url}}/suppliers/${provider}/products/{product_id}/variants" \
  -H "Authorization: Bearer $AGENT_API_KEY"

# Check stock for the primary variant
curl -s "{{api_base_url}}/suppliers/${provider}/stock/{variant_id}" \
  -H "Authorization: Bearer $AGENT_API_KEY"
```

Skip products where primary variant is out of stock.

**Checkpoint after Step 2:** report found candidates per product (name, variant ID,
price, stock status).

### Step 3 — Calculate shipping

For each in-stock candidate:

```bash
curl -s -X POST "{{api_base_url}}/suppliers/${provider}/shipping/calculate" \
  -H "Authorization: Bearer $AGENT_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "items": [{"variant_id": "{vid}", "quantity": 1, "shipping_method": "CJPacket"}],
    "shipping_address": {
      "country_code": "{target_country}",
      "province": "CA", "city": "Los Angeles", "zip_code": "90001",
      "address_line": "Test", "full_name": "Test", "phone": "+10000000000"
    }
  }'
```

Use a representative address in the target country for estimation.
For **CJ** in the US, preferred shipping methods: `CJPacket` > `ePacket` > `USPS`. For other providers, follow their supported method names from the supplier agent skill or API errors.

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
- **Budget API calls**: for N products, expect ~4N calls (search + variants + stock + shipping).
  If exceeding 6N, stop and report partial results.

## Guardrails

- Retry failed API calls at most twice.
- Do not confirm or create supplier orders — this skill is for research only.
  Purchasing is handled by the `supplier` agent via the catalog management workflow.
- Always include shipping cost in total cost calculations — never report source price alone.
- If a product has no images, flag it as a risk but do not auto-exclude (owner may accept).
- Note when stock quantity is null (some suppliers omit it) — flag as "stock unverified."

## Scope limits by effort

Read the effort level from the Agent Task instructions (`Effort: QUICK/STANDARD/DEEP`).
If not stated, use Standard.

| Limit | Quick | Standard | Deep |
|-------|-------|----------|------|
| Supplier candidates evaluated | 1 (top result) | 3 (top 3 with stock/ship) | 5-10 (full comparison) |
| Shipping methods compared | 1 (cheapest) | 1-2 (cheapest + fastest) | 3+ (all available) |
| Variant depth | Skip | Primary variant only | All variants with stock check |
| Browser supplier visits | 0 | 0 (fallback only) | 1-2 (for verification) |

## Fallback when Supplier API is unavailable

If `{{available_supplier_providers}}` is `(none)` or API returns errors:

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
