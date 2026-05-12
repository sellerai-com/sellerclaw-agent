---
name: tiktok-shop-research
description: "Evaluate TikTok Shop as a marketplace for a niche or product: search live TikTok Shop listings, fetch product detail (price, stock, related promo videos), and pull customer reviews via SociaVault-backed `sellerclaw` commands. Use when the task says 'is this on TikTok Shop', 'what's it cost on TikTok Shop', 'who sells this on TikTok Shop', 'TikTok Shop reviews for X', 'is X viral on TikTok Shop', or any task scoped to TikTok Shop listings/reviews specifically (not general TikTok content). Requires `research_social` configured (exit 2 / 503 otherwise). For broader TikTok/Reddit/Shorts trend mining use `social-trend-discovery`."
---

# TikTok Shop Research Skill

## Goal

Evaluate **TikTok Shop as a marketplace**: product discovery, pricing/stock signals, related promotional videos, and review themes. Requires `research_social` (SociaVault).

All commands are subcommands of `sellerclaw research-social …`. JSON on stdout; the `response` field carries the raw SociaVault payload alongside `provider`, `available_providers`, `credits_used`, `cost_usd`.

## Commands

| Command | Body fields | Purpose |
|---|---|---|
| `sellerclaw research-social post-tiktok-shop-search -b '<json>'` | `query` (required), optional `page`, `region` | Search TikTok Shop by query |
| `sellerclaw research-social post-tiktok-shop-product -b '<json>'` | `url` (required), optional `get_related_videos`, `region` | Product details + linked videos |
| `sellerclaw research-social post-tiktok-shop-reviews -b '<json>'` | `url` and/or `product_id`, optional `page` | Customer reviews |

Body schemas: `sellerclaw describe <op_id>` (e.g. `post_tiktok_shop_search_research_social_tiktok_shop_search_post`).

## Workflow

1. `post-tiktok-shop-search` with the niche or product keyword.
2. Pick candidates; call `post-tiktok-shop-product` for depth (related videos, stock hints).
3. `post-tiktok-shop-reviews` for customer voice and recurring complaints.
4. Compare with Amazon / Google Shopping signals from `product-demand-analysis` when available.

## Guardrails

- If the CLI exits with `503`, SociaVault is not configured — say so explicitly.
- Summarize reviews; avoid dumping PII or full review bodies unless necessary.
- Mention spend only when the owner asks (`cost_usd`).
