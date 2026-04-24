---
name: sales-channels
description: "List or fetch one sales channel via sellerclaw agent-sales-channels; use id / platform to scope other work to a storefront."
---

A **sales channel** is an **internal project entity** (a **DB row**): it holds SellerClaw’s data about an **external storefront** the user connected — integration state, identifiers, and display fields — not the live marketplace account itself.

## Channel fields (what to use)

| Field | Meaning |
|--------|---------|
| **`id`** | UUID → reuse as **`sales_channel_id`** / **`store_id`** in other **sellerclaw** calls for that shop. |
| **`platform`** | Marketplace type (API string; can grow). |
| **`status`** | Active, inactive, or needs credential refresh — check before integration-dependent work. |
| **`name`** | Label; align with how the owner names the shop. |
| **`domain`** | Storefront domain if set. |
| **`margin`** | Markup for this channel — how the owner prices sales in that store. |
| **`description`** | Free-text note. |

Ignore the rest of the payload unless the task explicitly needs it.

## Commands

**List** — choose by name / platform / domain / status:

```bash
sellerclaw agent-sales-channels list-sales-channels-for-user
```

Default is active-only; use this subcommand’s flag to include inactive or bad-auth channels when required.

**One row by id** — refresh a channel you already know:

```bash
sellerclaw agent-sales-channels get-sales-channel --sales-channel-id <id>
```

## Flow

1. No **`id`** → list, pick a row.  
2. Have **`id`** → get-sales-channel to confirm **status**.  
3. Pass **`id`** into any scoped follow-up work.
