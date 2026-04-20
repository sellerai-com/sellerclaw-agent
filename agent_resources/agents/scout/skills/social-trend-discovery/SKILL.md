---
name: social-trend-discovery
description: Discover product and niche momentum from TikTok, YouTube Shorts, and Reddit via SociaVault-backed sellerclaw-api.
---

# Social Trend Discovery Skill

## Goal

Surface **social-native demand signals** that search tools miss: viral TikTok patterns, trending Shorts, and Reddit discussions. Use when `research_social` (SociaVault) is configured.

## Base URL and authentication

- Base URL: `{{api_base_url}}`
- Auth header: `Authorization: Bearer $AGENT_API_KEY`
- Do not print token values in logs or messages.

## Conventions

- Use `exec curl` for HTTP requests.
- Request/response bodies are JSON.
- Responses include `provider`, `available_providers`, `credits_used`, `cost_usd`, and `response` (raw SociaVault JSON).
- If the API returns `503`, SociaVault is not configured; say so clearly.

## Endpoints (POST JSON)

| Endpoint | Purpose |
|----------|---------|
| `POST .../research/social/tiktok-popular-videos` | Popular TikTok videos (period, country, sort) |
| `POST .../research/social/tiktok-popular-hashtags` | Popular TikTok hashtags |
| `POST .../research/social/tiktok-trending` | TikTok trending feed |
| `POST .../research/social/tiktok-search` | Keyword search over TikTok videos |
| `POST .../research/social/youtube-trending-shorts` | Trending YouTube Shorts |
| `POST .../research/social/reddit-search` | Reddit-wide search |
| `POST .../research/social/reddit-subreddit` | Recent posts in a subreddit |

## Workflow

1. Start with `tiktok-popular-hashtags` or `tiktok-search` using seed keywords for the niche.
2. Drill into promising videos; note engagement proxies (views, likes) from `response`.
3. Cross-check with `youtube-trending-shorts` for Shorts momentum.
4. Use `reddit-search` / `reddit-subreddit` for pain points and language customers use.
5. Summarize: themes, rising formats, risks (fad vs sustained interest).

## Scope limits by effort

Read the effort level from the Agent Task instructions (`Effort: QUICK/STANDARD/DEEP`).
If not stated, use Standard.

| Limit | Quick | Standard | Deep |
|-------|-------|----------|------|
| SociaVault calls | 0-1 | 2-3 | 5-8 |
| Social platforms checked | 0 | 1-2 (TikTok + Reddit) | 3-4 (TikTok + Reddit + YouTube + TikTok Shop) |
| Browser social visits | 0 | 0 | 1-2 (TikTok hashtag, Reddit subreddit) |

## Fallback when SociaVault is unavailable

If the API returns `503` or `research_social` is not configured:

1. `web_search`: "{niche} tiktok trending 2026" — articles about TikTok trends.
2. `web_search`: "{product} viral tiktok" — find if product has social momentum.
3. `web_search`: "site:reddit.com {niche}" — Reddit discussions indexed by Google.
4. `web_search`: "{niche} social media trending product" — social commerce coverage.
5. Browser: visit TikTok (search hashtag), Reddit (search subreddit).

Return `tiktok_engagement` and `reddit_mentions` as `"unavailable"` if no signal
found via fallbacks. Do not fabricate social data.

When using web search fallbacks, note `"web_search"` in `data_sources_used` and
list SociaVault as unavailable in `data_gaps`.

## Guardrails

- Prefer a small number of API calls per task; batch hypotheses before calling.
- Credit cost scales with `credits_usd` / vendor usage — avoid redundant pagination.
- Never present scraped personal data as identifiable; summarize at theme level.
