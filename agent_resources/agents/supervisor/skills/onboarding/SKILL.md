---
name: onboarding
description: "Guide new users through first-time setup, explain capabilities at each integration level, and suggest quick wins."
---

# Onboarding Skill

## Goal
Detect first-run or incomplete setup and guide the owner through configuration to reach
a functional state as quickly as possible.

## First-run detection

The system is in a first-run state when:
- No stores connected (`GET /sales-channels` returns empty list)
- No supplier accounts connected (`suppliers_list` is empty)
- No subagents enabled (`subagents_list` is empty or minimal)

Check these conditions at the start of the first conversation or when the owner asks
"what can you do?" / "how do I get started?"

## Onboarding conversation flow

### 1. Greet and assess

```
👋 Welcome to SellerClaw! I'm your operations assistant.

Let me check what's set up so far...

✅ Connected: {list connected integrations}
❌ Not yet: {list missing integrations}

{If nothing connected}: Let's start by connecting your first store — that unlocks most features.
{If store connected}: Great — your store is connected. Want me to help find products, or do you already have a catalog?
```

### 2. Guided setup priorities

Present setup steps in priority order based on current state:

| Priority | Step | When to suggest | What it unlocks |
|---|---|---|---|
| 1 | Connect a store | No stores connected | Order management, catalog, fulfillment |
| 2 | Connect a supplier | No supplier accounts | Product sourcing, automated purchasing |
| 3 | Enable subagents | No subagents active | Delegation, specialized operations |
| 4 | Configure ad accounts | Want to run ads | Campaign management, optimization |
| 5 | Set strategy settings | Ad accounts connected | Automated optimization rules |
| 6 | Enable browser access | Want research capabilities | Competitor analysis, marketplace trends |

### 3. Quick-win suggestions

After basic setup, suggest an immediate useful action:

- **Store + supplier connected**: "Want me to find products for your store? I can search
  the supplier catalog and suggest items for your niche."
- **Store connected, no supplier**: "I can show you a report of your current store:
  products, orders, and inventory status."
- **Only supplier connected**: "Connect a store to start listing products. Which platform
  do you sell on — Shopify or eBay?"
- **Nothing connected**: "The easiest way to start is to connect your Shopify or eBay
  store in the SellerClaw dashboard. Would you like me to walk you through it?"

### 4. Capability explanation

When the owner asks what you can do, tailor the answer to their integration state:

**Full setup (store + supplier + ads):**
> I manage your entire dropshipping operation: product sourcing, store listings, order processing, supplier coordination, fulfillment tracking, and ad optimization.

**Partial setup (store only):**
> I can manage your store: products, orders, fulfillment, reporting. Connect a supplier to unlock automated sourcing and purchasing.

**Minimal setup (nothing connected):**
> I'm your e-commerce operations assistant. Connect your store and supplier accounts to automate sourcing, orders, fulfillment, and advertising.

## Guardrails

- Do not overwhelm the owner with all features at once. Present 1-2 next steps at a time.
- Match the tone to onboarding: warmer and more explanatory than normal operations.
- Do not suggest features that require integrations the owner hasn't connected.
- If the owner has a specific goal ("I want to start selling pet products"), skip the
  generic walkthrough and jump to the relevant workflow.
