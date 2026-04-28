---
name: listing-optimization
description: "Rewrite a marketplace listing (title, bullets, backend search terms) grounded in real search-behaviour data. Use when the user asks to improve, optimize, or rewrite a Shopify/eBay/Amazon listing, or to draft SEO-friendly copy for a new product."
---

# Listing Optimization Skill

## Goal

Turn search-intent signals into **actionable listing copy** for ecommerce titles,
descriptions, and tags — grounded in what people actually type and ask.

## Inputs

- Target marketplace (Shopify/eBay/etc.) and current draft title or bullet list.
- Primary seed keyword and 1–3 secondary terms.

## API workflow

1. **How people search** — `POST {{api_base_url}}/research/seo/autocomplete` for partial titles and category phrases.
2. **Questions to answer** — `POST .../people-also-ask` for the hero keyword; fold questions into bullets/FAQ.
3. **Volume sanity** — `POST .../keyword-volume` on final candidate phrases; prioritize phrases with meaningful volume and viable competition.

## Deliverables

- Recommended **title** (≤ platform limits) with primary keyword early.
- **Bullet outline** mapped to PAA themes.
- **Backend/search terms** list (synonyms, long-tail) deduped and volume-checked.

## Guardrails

- Never claim guaranteed ranking — these are hypotheses backed by data.
- If DataForSEO is unavailable (`503`), fall back to general SEO heuristics and label confidence **Low**.
- Keep claims compliant with marketplace policies (no medical/legal guarantees unless substantiated).
