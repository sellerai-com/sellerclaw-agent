---
name: supplier-search
description: "Gather product info at connected suppliers: search for products, look up price/variants/stock, get a shipping quote, or refresh data on a product we already know. Use when the owner says 'find this at the supplier', 'how much does X cost at CJ', 'is it in stock', 'how long is shipping', 'check the supplier for SKU X', 'refresh this supplier product', or any task asking for supplier-side product data without buying. Not for purchase (use `supplier-purchase`) or catalog writes (use `source-products`)."
---

# Supplier search

This skill describes how to delegate **information-gathering** about products to the `**supplier`** subagent — searches at connected suppliers, product detail / stock / shipping checks, refresh of already-known supplier products.

## Scope

- **Not for purchase, payment, tracking, or balance queries.**
- **Not for catalog DB writes.** Once the subagent returns candidates, persisting them belongs to `**source-products`**.
- **Not for storefront actions.** See `**store-products`**.

## Channel choice

- **Direct chat** (`**sessions_spawn`** / `**sessions_send**`) — small, exploratory, in-session lookups.
- **Task system** (`**task-management`** skill) — bulky, multi-step, trackable work; default for full sourcing rounds.

## Communication contract

**Subagent id:** `supplier`.

**Connected providers** depend on the workspace (e.g. `supplier_cj` → CJ Dropshipping). Pin a provider in the brief **only when** there is a reason to constrain it.

Every brief should contain, in plain language:

1. **Goal** — one sentence stating what information is needed and what it will be used for.
2. **Constraints** — only what actually narrows the search (target market, price band, variant requirements, MOQ, shipping options, time window, …). Skip whatever is not relevant.
3. **Required output shape** — fields the **caller's next step** actually consumes. The contract is **open** — the caller decides what is enough. Examples:
  - For saving to the catalog — ask for everything needed (canonical name / description / category / images, supplier identifiers, per-variation `supplier_variant_id`, `**available_quantity`**, `**purchase_price**`, `**shipping_cost**`).
  - For a comparison or a pick to present back to the requester — fewer fields can be enough (e.g. price + shipping + image + supplier link).
  - For a refresh / drift check — only the volatile fields (price, shipping, stock, availability flag).
4. **Acceptance criteria** — when to stop (top-1, ≥ N candidates, all targets covered, …).

**Validate the result against your downstream need**.

---

## If you need ONE best product per known item (targeted search)

**When:** the items are already named ("publish A, B, C, D"); you need one supplier match per item.

**Brief template** (everything in `< >` is optional — include only what is relevant):

```
> **Targeted supplier search.**
> <Provider: only if you need to pin one>
> Items (one query per item):
>   1. "<item description>"
>   2. ...
> Constraints:
>   - <target country, price band, required variants, ...>
> Required output per item:
>   - <list the fields the next step consumes>
> Acceptance criteria:
>   - Top-1 result per item, in stock, within constraints.
>   - If nothing matches an item — report it and skip, do NOT retry endlessly.
```

---

## If you need a candidate pool for a niche (broad search)

**When:** the request is "find products" / "fill the store" / "explore this niche" without exact items; you need a scored pool to pick from.

**Brief template:**

```
> **Broad supplier search.**
> <Provider: only if pinning>
> Niche / categories: <verbatim from the requester>
> Search terms: <list>
> Constraints:
>   - <target country, price band, minimum candidates, ...>
> Required output per candidate:
>   - <fields the next step consumes>
>   - <ask for the subagent's score / quality signal if you will rank them>
> Acceptance criteria:
>   - At least <N> usable candidates / cover all categories; otherwise return what was found and stop.
```

---

## If you need fresh data for already-known supplier products (refresh)

**When:** investigating drift, a stock/price anomaly, or a check on specific catalog rows. Routine stock/price sync is system-automated — use this only for exceptions.

**Inputs:** look up `**supplier_provider`** + `**supplier_product_id**` + per-variation `**supplier_variant_id**` for the target rows via `**source-products**` (`get` / `list`) **before** delegating.

**Brief template:**

```
> **Refresh supplier product data.**
> Targets:
>   - supplier_provider: <code>, supplier_product_id: <id>, variants: [<supplier_variant_id>, ...]
>   - ...
> Requested fields: <e.g. current purchase_price, shipping_cost, available_quantity, availability flag, image set>
> Reason: <stock anomaly / drift check / ...>
```

**On return:** diff against the source of truth, route updates through the right skill — `**source-products`** for catalog metadata, `**store-products**` for storefront-side stock/price actions.

---

## Failure handling

- **No usable result for an item** → report the item, skip, continue with the rest.
- **Result missing fields the caller's next step requires** → reject with feedback; never patch the gap manually.
- **Pinned provider not connected** → STOP and report it; do not silently fall back to another provider.
- **Subagent silent for too long** → check via `**sessions_history`** / `**get-timeline**`; one nudge before escalating, no spamming.

## Constraints

- The subagent never talks to the owner directly — the **caller** synthesizes the result.
- Never invent supplier ids / prices / stock to "fill in" missing fields.
- Do not prescribe supplier endpoints, payload shapes, or page sizes — the subagent owns those.

