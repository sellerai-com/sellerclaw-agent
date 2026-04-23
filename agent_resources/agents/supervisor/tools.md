# TOOLS.md — Local notes

## Agent API

- **Base URL:** `{{api_base_url}}`
- **Auth:** env `AGENT_API_KEY` / bearer for HTTP — never paste keys into chat or this file.

---

## Channels & product toggles

- **Primary channel:** {{primary_channel}}
- **Browser tool:** {{global_browser_enabled}}
- **Web search:** {{web_search_enabled}}

---

## Stores & suppliers (manifest snapshot)

Use live APIs for authority; this block mirrors the bundle manifest.

### Stores

{{stores_list}}

### Suppliers

{{suppliers_list}}

---

## Subagents (manifest snapshot)

{{subagents_list}}

---

## Strategy / supplier hints (manifest)

{{ad_strategy_settings}}

**Available supplier providers (manifest):** {{available_supplier_providers}}

---

## Local paths (defaults; override via `template_variables` if needed)

- **Browser screenshots / media:** `{{tools_browser_media_root}}`
- **Temp exports:** `{{tools_temp_exports_root}}`

