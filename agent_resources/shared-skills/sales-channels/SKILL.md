---
name: sales-channels
description: "Look up connected storefronts (Shopify, eBay, etc.): list all of them, filter by platform, fetch a specific store, or check its connection status. Use when the owner asks 'which stores do I have', 'is Shopify connected', 'what's the status of eBay', 'which shops are linked', or whenever another skill needs to resolve a store by name/domain to its internal id and platform before acting on it."
---

A **sales channel** is a user's online store on a marketplace platform (Shopify, eBay, etc.) connected to SellerClaw. The user may have multiple sales channels across different platforms.

**Owner-facing copy:** When you talk *to* the user about "which store", refer to it by `name` or `domain`, not by the internal `id` (UUID).

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

**Filter by platform (e.g. only Shopify)** — pass the `platform` query (always lowercase, matches the API: `shopify`, `ebay`, …):

```bash
sellerclaw agent-sales-channels list-for-user --platform shopify
```

**Status filter** — repeat `--status` for multiple values (e.g. include accounts that need credential refresh):

```bash
sellerclaw agent-sales-channels list-for-user --status active --status credentials_invalid
```

**One row by id** — pass the UUID as a **positional** argument:

```bash
sellerclaw agent-sales-channels get <sales_channel_id>
```
