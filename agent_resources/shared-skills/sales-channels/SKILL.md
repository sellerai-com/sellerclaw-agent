---
name: sales-channels
description: "Look up connected storefronts (Shopify, eBay, etc.): list, filter by platform, fetch by id, check status."
---

A **sales channel** is a user's online store on a marketplace platform (Shopify, eBay, etc.) connected to SellerClaw. The user may have multiple sales channels across different platforms.

**Owner-facing copy:** When you talk *to* the user about “which store”, refer to it by `**name` or `domain`**, not by the internal `**id**` (UUID).

## Channel model


| Field         | Meaning                                                                                      |
| ------------- | -------------------------------------------------------------------------------------------- |
| `id`          | UUID → reuse as `sales_channel_id` / `store_id` in other **sellerclaw** calls for that shop. |
| `platform`    | Marketplace type (API string; can grow).                                                     |
| `status`      | Active, inactive, or needs credential refresh — check before integration-dependent work.     |
| `name`        | Label; align with how the owner names the shop.                                              |
| `domain`      | Storefront domain if set.                                                                    |
| `margin`      | Markup for this channel — how the owner prices sales in that store.                          |
| `description` | Free-text note.                                                                              |


**More detail (read on demand, not by default):** full field list, exact `status` values, and **background** behavior — references/channel-record.md.

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
sellerclaw agent-sales-channels list-for-user --active_only=false
```

**One row by id** — pass the UUID as a **positional** argument:

```bash
sellerclaw agent-sales-channels get <sales_channel_id>
```
