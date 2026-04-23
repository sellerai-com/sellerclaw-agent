---
name: tiktok-shop-research
description: "Research TikTok Shop listings, product detail, and reviews via SociaVault-backed sellerclaw-api."
---

# TikTok Shop Research Skill

## Goal

Evaluate **TikTok Shop as a marketplace**: product discovery, pricing/stock signals, related promotional videos, and review themes. Requires `research_social` (SociaVault).

## Base URL and authentication

- Base URL: `{{api_base_url}}`
- Auth header: `Authorization: Bearer $AGENT_API_KEY`

## Endpoints (POST JSON)

| Endpoint | Purpose |
|----------|---------|
| `POST .../research/social/tiktok-shop-search` | Search TikTok Shop by query (`query`, optional `page`, `region`) |
| `POST .../research/social/tiktok-shop-product` | Product details (`url` required; optional `get_related_videos`, `region`) |
| `POST .../research/social/tiktok-shop-reviews` | Product reviews (`url` and/or `product_id`, optional `page`) |

## Response shape

- `provider`, `available_providers`, `credits_used`, `cost_usd`, `response` (vendor JSON).
- Parse fields from `response` / nested `data` per SociaVault docs (titles, price, stock, linked videos, review text).

## Workflow

1. `tiktok-shop-search` with the niche or product keyword.
2. Pick candidates; call `tiktok-shop-product` for depth (related videos, stock hints).
3. `tiktok-shop-reviews` for customer voice and recurring complaints.
4. Compare with Amazon / Google Shopping signals from `product-demand-analysis` when available.

## Guardrails

- Respect `503` when SociaVault is not configured.
- Summarize reviews; avoid dumping PII or full review bodies unless necessary.
- Mention spend only when the owner asks (`cost_usd`).
