---
name: sales-channels
description: "Look up the sales channels (connected storefronts) for this workspace, or fetch one by id. Use when the user says \"which stores do we have\", \"show the Shopify store\", \"is the eBay account connected\", \"what's the margin on the <store>\", or whenever another task needs to target a specific storefront."
---

A **sales channel** is a user's online store on a marketplace platform (Shopify, eBay, etc.) connected to SellerClaw. The user may have multiple sales channels across different platforms.

## Channel model

| Field | Meaning |
|--------|---------|
| `id` | UUID → reuse as `sales_channel_id` / `store_id` in other **sellerclaw** calls for that shop. |
| `platform` | Marketplace type (API string; can grow). |
| `status` | Active, inactive, or needs credential refresh — check before integration-dependent work. |
| `name` | Label; align with how the owner names the shop. |
| `domain` | Storefront domain if set. |
| `margin` | Markup for this channel — how the owner prices sales in that store. |
| `description` | Free-text note. |

Ignore the rest of the payload unless the task explicitly needs it.

## Commands

**List** — choose by name / platform / domain / status in the returned JSON:

```bash
sellerclaw agent-sales-channels list-for-user
```

**Filter by platform (e.g. only Shopify)** — pass the `platform` query. Prefer the typed subcommand (see `list-for-user --help` for the exact flag name, typically `--platform`):

```bash
sellerclaw agent-sales-channels list-for-user --platform shopify
```

Values match the API (e.g. `shopify`, `ebay`).


**Active only** — default is **active-only** channels. To include inactive or credential-invalid rows:

```bash
sellerclaw call list_sales_channels_for_user_sales_channels_get -q active_only=false
```

**One row by id** — pass the UUID as a **positional** argument:

```bash
sellerclaw agent-sales-channels get <sales_channel_id>
```

## Flow

1. No `id` → `list-for-user` (optionally with **platform**), pick a row.
2. Have `id` → `get` to confirm **status**.
3. Pass `id` into any scoped follow-up work.
