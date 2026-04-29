---
name: listing-optimization
description: "Rewrite a marketplace listing (title, bullets, backend search terms) grounded in real search-behaviour data. Use when the user asks to improve, optimize, or rewrite a Shopify/eBay/Amazon listing, or to draft SEO-friendly copy for a new product."
---

# Listing Optimization Skill

## Goal

Turn search-intent signals into **actionable listing copy** for ecommerce titles, descriptions, and tags — grounded in what people actually type and ask.

## Inputs

- Target marketplace (Shopify/eBay/etc.) and current draft title or bullet list.
- Primary seed keyword and 1–3 secondary terms.

## Workflow (via `sellerclaw` CLI)

1. **How people search** — `sellerclaw research-seo post-autocomplete -b '<json>'` for partial titles and category phrases.
2. **Questions to answer** — `sellerclaw research-seo post-people-also-ask -b '<json>'` for the hero keyword; fold questions into bullets/FAQ.
3. **Volume sanity** — `sellerclaw research-seo post-keyword-volume -b '<json>'` on final candidate phrases; prioritize phrases with meaningful volume and viable competition.

Body schemas: `sellerclaw describe <op_id>` (e.g. `post_people_also_ask_research_seo_people_also_ask_post`).

## Deliverables

- Recommended **title** (≤ platform limits) with primary keyword early.
- **Bullet outline** mapped to PAA themes.
- **Backend/search terms** list (synonyms, long-tail) deduped and volume-checked.

## Guardrails

- Never claim guaranteed ranking — these are hypotheses backed by data.
- If DataForSEO is unavailable (CLI exit 2 / 503), fall back to general SEO heuristics and label confidence **Low**.
- Keep claims compliant with marketplace policies (no medical/legal guarantees unless substantiated).
