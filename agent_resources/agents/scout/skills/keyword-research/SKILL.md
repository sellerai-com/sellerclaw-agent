---
name: keyword-research
description: "Expand and filter keywords for niche or product discovery using DataForSEO autocomplete, volume, and related-keyword APIs."
---

# Keyword Research Skill

## Goal

Systematically grow a seed term into a ranked keyword list: suggestions → volumes →
related terms — using `sellerclaw-api` DataForSEO endpoints when `research_seo` is active.

## Base URL and authentication

- Base URL: `{{api_base_url}}`
- Header: `Authorization: Bearer $AGENT_API_KEY`
- All bodies below are JSON (`Content-Type: application/json`).

## Recommended workflow

1. **Autocomplete** — `POST {{api_base_url}}/research/seo/autocomplete` with `keyword` (partial term) and `location_code` / `language_code` as needed. Collect high-relevance suggestions.
2. **Volume** — `POST .../keyword-volume` for batches of terms to get monthly search volume and competition signals. Drop terms below your floor.
3. **Expansion** — `POST .../keyword-ideas` from the best seeds to pull related keywords; re-run volume on promising new terms.
4. **Output** — deliver a table: keyword, volume, competition, notes (intent: commercial / informational).

## Endpoints (summary)

| Method | Path | Role |
|---|---|---|
| POST | `/research/seo/autocomplete` | Google autosuggest strings |
| POST | `/research/seo/keyword-volume` | Google Ads search volumes |
| POST | `/research/seo/keyword-ideas` | Labs related keywords |

## Guardrails

- Prefer cached responses; avoid duplicate calls for the same payload within one task.
- If the API returns `503`, state that DataForSEO is not configured and fall back to trends or browser research per capabilities.
- Do not echo raw API passwords or full request auth headers.
