---
name: shopify-storefront-setup
description: Set up and customize Shopify storefront — pages, collections, navigation, shop settings, and themes via sellerclaw-api.
---

# Shopify storefront setup

## Goal
Take an empty or minimal Shopify store toward a production-ready online storefront: branding copy, collections, informational pages, navigation, optional theme work, then products linked to collections.

## Capability awareness (read `{{capabilities_modes}}` first)

- **`storefront_content: autonomous`** — use the **content** HTTP endpoints below (`store_id` = sales channel UUID).
- **`storefront_content: assisted`** — same work via **browser** in Shopify Admin (Online Store → Pages / Navigation / Collections).
- **`storefront_content: advisory`** — provide structure, copy, and checklist only.
- **`theme_customization: autonomous`** — Theme API endpoints (list/create/publish/files) are available **only** when Theme API is enabled for the user **and** the integration bundle exposes Theme API access.
- **`theme_customization: assisted`** — customize the live theme in **Shopify Admin → Online Store → Themes → Customize** (Theme Editor).
- **`theme_customization: advisory`** — explain Online Store 2.0 structure, Dawn sections, and safe rollout steps without calling Theme API.

Base URL: `{{api_base_url}}` — use `Authorization: Bearer $AGENT_API_KEY`. Prefer `exec curl` for JSON APIs.

## Storefront content — HTTP endpoints

All paths are under **`/agent/stores/{store_id}/...`** where `store_id` is the **sales channel UUID** (same as in `GET /sales-channels`). Do not use `*.myshopify.com` in the URL path.

### Pages
| Method | Path | Body / notes |
| --- | --- | --- |
| GET | `/pages` | Query: `limit`, `after`, `query` |
| POST | `/pages` | `{ "title", "body"?, "handle"?, "is_published"?, "template_suffix"? }` |
| PUT | `/pages/{page_id}` | Partial `{ "title"?, "body"?, "handle"?, "is_published"?, "template_suffix"? }` |
| DELETE | `/pages/{page_id}` | |

### Collections
| Method | Path | Body / notes |
| --- | --- | --- |
| GET | `/collections` | Query: `limit`, `after`, `query` |
| POST | `/collections` | GraphQL `CollectionInput` as JSON (`title` required; `ruleSet` for smart collections) |
| POST | `/collections/{collection_id}/products` | `{ "product_ids": ["…"] }` (manual collections) |

### Navigation
| Method | Path | Body / notes |
| --- | --- | --- |
| GET | `/navigation/menus` | Query: `limit`, `after`, `query` |
| POST | `/navigation/menus` | `{ "title", "handle", "items": [ MenuItemCreateInput… ] }` |
| PUT | `/navigation/menus/{menu_id}` | `{ "title", "handle"?, "items": [ MenuItemUpdateInput… ] }` |

### Shop settings (legacy REST)
| Method | Path | Body |
| --- | --- | --- |
| PUT | `/shop/settings` | `{ "name"?, "email"?, "customer_email"? }` — forwarded as `PUT /admin/api/.../shop.json`. If Shopify rejects the update, fall back to Admin UI or advisory. |

## Theme customization — HTTP endpoints (autonomous only)

| Method | Path | Body / notes |
| --- | --- | --- |
| GET | `/themes` | List themes |
| POST | `/themes` | `{ "source": "<zip url>", "name"?, "role"? }` |
| POST | `/themes/{theme_id}/publish` | |
| GET | `/themes/{theme_id}/files` | Query: `filenames` (repeatable), `limit`, `after` |
| PUT | `/themes/{theme_id}/files` | `{ "files": [ { "filename", "body": { "type": "TEXT"\|"BASE64"\|"URL", "value" } } ] }` — **max 50 files** per request |

## Online Store 2.0 — JSON templates (autonomous theme mode)

- `templates/*.json` — JSON templates declare `sections` and `order`.
- `config/settings_data.json` — theme settings; changing it affects global appearance.
- Prefer **small batches**: use `themeFilesUpsert` with **≤ 50 files** per call; repeat for larger changes.
- **Dawn-style sections** often used on home: `image-banner`, `rich-text`, `featured-collection`, `multicolumn`, `newsletter` — names vary by theme.

## Storefront setup playbook (recommended order)

1. Shop settings (name, contact emails) when REST update is accepted.
2. Collections (manual or smart) for merchandising structure.
3. Pages: About, Shipping, FAQ, Contact, Policies as needed.
4. Navigation: header + footer menus linking to collections, pages, external URLs.
5. Theme: pick/publish or customize JSON templates **if** `theme_customization: autonomous`; otherwise Theme Editor (assisted) or guidance (advisory).
6. Products: create/import and attach to collections (`/collections/{id}/products`).

## Browser fallback — Theme Editor (assisted)

1. Shopify Admin → **Online Store** → **Themes**.
2. On the active (or draft) theme, click **Customize**.
3. Use the sidebar to edit sections; for JSON templates, use **Edit code** only when comfortable with theme file structure.
4. **Preview** before saving; avoid deleting the main published theme.

## Guardrails

- Do **not** delete the only **published** / **MAIN** theme without a replacement.
- Before **publish**, ensure a rollback path (duplicate theme or export).
- Respect scope limits: Theme file writes require Theme API exemption from Shopify.
