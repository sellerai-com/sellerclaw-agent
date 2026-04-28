---
name: shopify-storefront-setup
description: "Set up Shopify storefront content: pages, collections, navigation menus, shop settings, and theme. Use when the task is non-product (e.g. 'add an About page', 'create a collection', 'set up the main menu', 'change the theme'). For products and listings use `shopify-products`."
---

# Shopify storefront setup

**Assumption:** `store_id` (sales-channel UUID for the target Shopify shop) is **already known** from the task or session context. If it is missing or ambiguous, resolve it first (e.g. `sellerclaw sales-channels list-sales-channels`).

## Scope

This skill covers **non-product storefront content** only:

- **Pages** (About, Shipping, FAQ, Contact, Policies, …)
- **Collections** (manual / smart) and product-to-collection attachment.
- **Navigation menus** (header, footer, custom).
- **Shop settings** (name, contact emails).
- **Theme** (list / inspect / create / publish / delete / file CRUD) — only when the capability allows.

## Capability awareness (read `{{capabilities_modes}}` first)

- `storefront_content: autonomous` — call the `sellerclaw` CLI commands below directly.
- `storefront_content: assisted` — do the same work via **browser** in Shopify Admin (Online Store → Pages / Navigation / Collections).
- `storefront_content: advisory` — provide structure, copy, and checklist only — no calls.
- `theme_customization: autonomous` — Theme commands below are available **only** when Theme API is enabled for this user **and** the integration bundle exposes it.
- `theme_customization: assisted` — customize the live theme in **Shopify Admin → Online Store → Themes → Customize** (Theme Editor).
- `theme_customization: advisory` — explain Online Store 2.0 structure, sections, and safe rollout steps without calling Theme API.

## CLI conventions

- All commands run as `sellerclaw stores <command> <store_id> [flags]`. `store_id` is the **sales-channel UUID** (as returned by `sellerclaw sales-channels list-sales-channels`).
- Commands that take a JSON body accept `--json-body` / `-b`:
  - inline literal: `-b '{"title": "About"}'`
  - file: `-b @/path/to/body.json`
  - stdin: `-b @-`
- For any command marked **(JSON body)** below, the **exact request schema** is available via `sellerclaw describe <operation_id>` — the operation id is shown in `sellerclaw stores <command> --help`. Always run `describe` before composing a non-trivial body.
- Use `sellerclaw stores <command> --help` for argument and option details.

Pick the section by **task intent** below.

---

## If you need to manage informational pages

**When:** "add an About page", "edit the Shipping page", "remove a draft page".

| Action  | Command                                                  |
| ------- | -------------------------------------------------------- |
| List    | `sellerclaw stores list-pages <store_id>`                |
| Create  | `sellerclaw stores create-page <store_id>` **(JSON body)** |
| Update  | `sellerclaw stores update-page <store_id> <page_id>` **(JSON body)** — partial |
| Delete  | `sellerclaw stores delete-page <store_id> <page_id>`     |

**List options:** `--limit`, `--after` (pagination cursor), `--query` (text filter).

**Body fields (create / update — update is partial: omit a field to leave it unchanged):**

- `title` (required on create) — page title shown to buyers.
- `body` (optional, HTML) — page content.
- `handle` (optional) — URL slug; auto-derived from title when omitted.
- `is_published` (optional, bool) — whether the page is live on the storefront.
- `template_suffix` (optional) — alternate Liquid template handle (e.g. `contact`).

---

## If you need to manage collections (manual / smart)

**When:** "create a Sale collection", "add these products to Best Sellers", "rename a collection", "remove a collection".

| Action            | Command                                                                       |
| ----------------- | ----------------------------------------------------------------------------- |
| List              | `sellerclaw stores list-collections <store_id>`                               |
| Create            | `sellerclaw stores create-collection <store_id>` **(JSON body)**              |
| Update            | `sellerclaw stores update-collection <store_id> <collection_id>` **(JSON body)** |
| Delete            | `sellerclaw stores delete-collection <store_id> <collection_id>`              |
| Attach products   | `sellerclaw stores add-collection-products <store_id> <collection_id>` **(JSON body)** |
| Detach products   | `sellerclaw stores remove-collection-products <store_id> <collection_id>` **(JSON body)** |

**List options:** `--limit`, `--after`, `--query`.

**Body fields (create / update) — GraphQL `CollectionInput` as JSON:**

- `title` (required on create) — collection name.
- `descriptionHtml` (optional, HTML) — collection description.
- `handle` (optional) — URL slug.
- `ruleSet` (optional) — present for **smart** collections (automatic rules); omit for manual collections.
- `image`, `seo`, `sortOrder` (optional) — see Shopify's `CollectionInput` reference for the exact shape.

**Body (attach / detach products):**

- `product_ids` (required, array of strings) — Shopify Admin **product** ids to add or remove. Manual collections only.

**Guardrails:**

- `delete-collection` is irreversible from the storefront perspective — confirm intent and surface the collection name back to the user before calling.
- For smart collections, edit by overwriting `ruleSet` via `update-collection`; do not try to attach/detach products manually.

---

## If you need to manage the storefront navigation menus

**When:** "set up header menu", "edit footer links", "change main menu structure", "delete a menu".

| Action      | Command                                                          |
| ----------- | ---------------------------------------------------------------- |
| List menus  | `sellerclaw stores list-menus <store_id>`                        |
| Create menu | `sellerclaw stores create-menu <store_id>` **(JSON body)**       |
| Update menu | `sellerclaw stores update-menu <store_id> <menu_id>` **(JSON body)** |
| Delete menu | `sellerclaw stores delete-menu <store_id> <menu_id>`             |

**List options:** `--limit`, `--after`, `--query`.

**Body fields (create / update):**

- `title` (required on create) — display title (e.g. *Main menu*, *Footer*).
- `handle` (required on create, optional on update) — stable handle (e.g. `main-menu`, `footer`).
- `items` (required) — ordered array of menu entries (`MenuItemCreateInput` on create, `MenuItemUpdateInput` on update). Per item:
  - `title` — visible label.
  - `type` — link target kind: `COLLECTION` | `PAGE` | `PRODUCT` | `HTTP` | `FRONTPAGE` | … .
  - `resourceId` / `url` — destination depending on `type`.
  - `items` (optional) — nested children (submenu).

**Guardrail:** never `delete-menu` the only header or footer menu without first preparing a replacement — the storefront falls back to a default layout that may hide important links.

---

## If you need to read or update shop settings (name, contact emails)

**When:** "rename the shop", "change contact email", "what's the current shop email".

| Action | Command                                                            |
| ------ | ------------------------------------------------------------------ |
| Read   | `sellerclaw stores get-shop-settings <store_id>`                   |
| Update | `sellerclaw stores update-shop-settings <store_id>` **(JSON body)** |

`update-shop-settings` is forwarded to legacy `PUT /admin/api/.../shop.json`. `get-shop-settings` reads the broader GraphQL `shop` object — use it to confirm current values before an update.

**Body (update — all optional, omit a field to leave it unchanged):**

- `name` — shop display name.
- `email` — main contact email.
- `customer_email` — customer-facing reply-to address.

If Shopify rejects the update, fall back to **assisted** (Admin UI) or **advisory** mode.

---

## If you need to customize the theme (autonomous Theme API only)

**When:** `theme_customization: autonomous` **and** Theme API is exposed by the bundle. Otherwise see *Browser fallback* below.

| Action          | Command                                                                          |
| --------------- | -------------------------------------------------------------------------------- |
| List themes     | `sellerclaw stores list-themes <store_id>`                                       |
| Get one theme   | `sellerclaw stores get-theme <store_id> <theme_id>`                              |
| Create theme    | `sellerclaw stores create-theme <store_id>` **(JSON body)**                      |
| Publish theme   | `sellerclaw stores publish-theme <store_id> <theme_id>`                          |
| Delete theme    | `sellerclaw stores delete-theme <store_id> <theme_id>`                           |
| Read files      | `sellerclaw stores get-theme-files <store_id> <theme_id>`                        |
| Upsert files    | `sellerclaw stores upsert-theme-files <store_id> <theme_id>` **(JSON body)**     |
| Delete files    | `sellerclaw stores delete-theme-files <store_id> <theme_id>` **(JSON body)**     |

**Body (create theme):**

- `source` (required) — theme **zip URL**.
- `name` (optional) — display name.
- `role` (optional) — `MAIN` to publish on create; otherwise leave default for an unpublished theme.

**Read-files options:** `--filenames` (repeatable, exact filename match), `--limit`, `--after`.

**Body (upsert-theme-files):**

- `files` (required, array, **≤ 50** per request) — files to upsert. Per file:
  - `filename` (required) — path inside the theme (e.g. `templates/index.json`).
  - `body` (required) — content envelope:
    - `type` (required) — `TEXT` | `BASE64` | `URL`.
    - `value` (required) — text contents, base64 blob, or URL to fetch.

**Body (delete-theme-files):**

- `filenames` (required, array, **≤ 50** per request) — filenames to remove from the theme.

**Online Store 2.0 notes:**

- `templates/*.json` declare `sections` + `order` for each page template.
- `config/settings_data.json` is global theme settings — changes affect appearance everywhere.
- Common Dawn-style home sections: `image-banner`, `rich-text`, `featured-collection`, `multicolumn`, `newsletter` (names vary by theme).
- Prefer **small batches**: ≤ 50 files per upsert / delete; repeat for larger rollouts.

**Guardrails:**

- Do **not** `delete-theme` the only **published** / **MAIN** theme without a replacement.
- Before `publish-theme`, ensure a rollback path: duplicate via `create-theme` from a downloaded zip, or keep the previous theme unpublished but available.
- Theme file writes / deletes require Theme API exemption from Shopify — honor the capability mode and stop if it is not autonomous.

---

## If you need to bring a fresh storefront to a sellable state (recommended order)

1. **Shop settings** — `get-shop-settings` first to read current values, then `update-shop-settings` for name and contact emails (when REST update is accepted).
2. **Collections** — `create-collection` (manual or smart), for merchandising structure.
3. **Pages** — `create-page` for About, Shipping, FAQ, Contact, Policies as needed.
4. **Navigation** — `create-menu` / `update-menu` for header + footer linking to collections, pages, external URLs.
5. **Theme** — pick / publish or customize JSON templates with `list-themes` → `get-theme-files` → `upsert-theme-files` → `publish-theme` if `theme_customization: autonomous`; otherwise Theme Editor (assisted) or guidance (advisory).
6. **Products** — attach existing Shopify products to collections via `add-collection-products`. Creating products and publishing them as listings lives in the `shopify-products` skill — do not duplicate that work here.

---

## Browser fallback (assisted mode)

When `storefront_content: assisted` or `theme_customization: assisted`:

1. Shopify Admin → **Online Store** → relevant area (**Pages** / **Navigation** / **Collections** / **Themes**).
2. For themes: on the active or draft theme, click **Customize**; use the sidebar to edit sections; use **Edit code** for JSON templates only when comfortable with the theme file structure.
3. **Preview** before saving; never delete the only published theme.
