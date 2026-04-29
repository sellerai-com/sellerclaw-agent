---
name: keyword-research
description: "Grow a seed term into a ranked keyword list via DataForSEO commands in the `sellerclaw` CLI: autocomplete suggestions, monthly search volume + competition, and related/labs ideas. Use when the user asks for keyword ideas, wants to size demand for terms, needs long-tail or backend search-term lists, or is preparing inputs for listing copy or ad targeting. Requires `research_seo` configured (exit code 2 / 503 otherwise); for trend direction over time use `trend-analysis`, for listing copy use `listing-optimization`."
---

# Keyword Research Skill

## Goal

Systematically grow a seed term into a ranked keyword list: suggestions → volumes → related terms — using `sellerclaw research-seo …` when `research_seo` is active. JSON on stdout, structured errors on stderr (exit 1=user/api, 2=server/network, 3=auth).

## Recommended workflow

1. **Autocomplete** — `sellerclaw research-seo post-autocomplete -b '<json>'` with `keyword` (partial term) and `location_code` / `language_code` as needed. Collect high-relevance suggestions.
2. **Volume** — `sellerclaw research-seo post-keyword-volume -b '<json>'` for batches of terms; drop terms below your floor on monthly volume / competition.
3. **Expansion** — `sellerclaw research-seo post-keyword-ideas -b '<json>'` from the best seeds; re-run volume on promising new terms.
4. **Output** — deliver a table: keyword, volume, competition, notes (intent: commercial / informational).

## Commands (summary)

| Command | Role |
|---|---|
| `sellerclaw research-seo post-autocomplete` | Google autosuggest strings |
| `sellerclaw research-seo post-keyword-volume` | Google Ads search volumes |
| `sellerclaw research-seo post-keyword-ideas` | Labs related keywords |

Body schemas: `sellerclaw describe <operation_id>` (op IDs: `post_autocomplete_research_seo_autocomplete_post`, `post_keyword_volume_research_seo_keyword_volume_post`, `post_keyword_ideas_research_seo_keyword_ideas_post`).

## Guardrails

- Avoid duplicate calls for the same payload within one task.
- If the CLI exits with `503`/server error, state that DataForSEO is not configured and fall back to `trend-analysis` or browser research per capabilities.
- Never echo auth tokens.
