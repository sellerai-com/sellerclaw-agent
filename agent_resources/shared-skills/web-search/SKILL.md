---
name: web-search
description: "How to use the `web_search` tool to look up facts, clarify ambiguities, and research the open web. Read before the first web lookup in a session, or whenever search results look off."
---

# Web Search

`web_search` is the primary way to consult the open web — clarifying user input, verifying facts, finding URLs, researching. It is **not** a substitute for first-party integrations, do **not** use it to bypass an existing API (catalog, SEO, social, etc.): prefer `sellerclaw` operations whenever the data lives behind one.

The SellerClaw backend dispatches queries to one of the configured providers (**Brave**, **Tavily**, or **Serper**); the agent does not choose the provider and does not need API keys.

## Usage tips

- `search_lang` takes a 2-letter code (`"en"`, not `"en-US"`); for geo filtering pair it with `country` (a 2-letter code, e.g. `"US"`).
- Quote exact phrases (`"product name"`); restrict with `site:` (e.g. `site:amazon.com`); add the current year for freshness; add `price` / `$` for cost data.
- Always pull URLs from search results — never construct them by guessing.
