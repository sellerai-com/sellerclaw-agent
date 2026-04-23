---
name: product-enrichment-delegation
description: "Recognize a \"my product card is incomplete — help me fill it\" request and delegate enrichment to the Product Scout agent. Do not perform enrichment yourself; Scout has the right tool/skill profile."
---

# Product Enrichment — Supervisor Delegation

## When to delegate

Delegate to Product Scout when the user:

- asks to publish/create a product of a known brand but did not provide a full card;
- gives only a barcode (EAN/UPC/GTIN/ISBN) and asks to "find and fill in the rest";
- is unsure about the description/brand/model correctness and asks to "verify against open sources";
- asks to aggregate product data from multiple sources for publishing.

**Do not delegate when:**

- the user already provided a full card and only asks to publish it — that goes directly to store-manager skills;
- the request is about trends/niches/SEO — route via `product-scout-delegation` (for other research tasks) or the appropriate marketing skill.

## What to pass in the delegation

When spawning Product Scout, include at minimum:

- what is known about the product: `brand`, `model`, `gtin` (any subset);
- the target marketplace/publishing channel if known — this hints at `marketplace_id` for eBay Browse (`EBAY_US` / `EBAY_GB` / `EBAY_DE` / ...);
- the enrichment goal: "prepare data for publishing on <channel>" or "verify the existing card".

Scout will pick the sources itself (primarily the `product-enrichment` skill backed by eBay Browse) and return aggregated fields.

## After Scout returns

Once Scout hands back the enriched card:

1. Show the user which fields were filled and which stayed empty.
2. If anything important is still missing (dimensions, material, specific attributes) — ask the user, do not invent values.
3. Do not overwrite fields the user has already set explicitly — only fill gaps.

## Cost note

The primary source (eBay Browse) is free and does **not** pass through billing on our side. Other fallback sources (Brave/Tavily web search) may consume credits — if the user has paid-source limits enabled, pass the constraint down to Scout.
