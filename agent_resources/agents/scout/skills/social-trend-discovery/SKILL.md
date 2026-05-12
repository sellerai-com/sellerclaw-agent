---
name: social-trend-discovery
description: "Surface social-native demand signals that search tools miss — popular/trending TikTok videos and hashtags, YouTube Shorts, Reddit threads — through SociaVault-backed `sellerclaw research-social` commands. Use when the task says 'is X viral on TikTok', 'what's trending now', 'check Reddit for this niche', 'find momentum signals', 'is anyone talking about this on social', 'find early signals before search volume catches up', or any task about social/early-stage demand. Requires `research_social` configured (exit 2 / 503 otherwise). For TikTok Shop listings/reviews specifically use `tiktok-shop-research`."
---

# Social Trend Discovery Skill

## Goal

Surface **social-native demand signals** that search tools miss: viral TikTok patterns, trending Shorts, and Reddit discussions. Use when `research_social` (SociaVault) is configured.

All commands are subcommands of `sellerclaw research-social …`. JSON on stdout; each response includes `provider`, `available_providers`, `credits_used`, `cost_usd`, and `response` (raw SociaVault JSON). CLI exit `2` / `503` = SociaVault not configured — say so clearly.

## Commands

| Command | Purpose |
|---|---|
| `sellerclaw research-social post-tiktok-popular-videos -b '<json>'` | Popular TikTok videos (period, country, sort) |
| `sellerclaw research-social post-tiktok-popular-hashtags -b '<json>'` | Popular TikTok hashtags |
| `sellerclaw research-social post-tiktok-trending -b '<json>'` | TikTok trending feed |
| `sellerclaw research-social post-tiktok-search -b '<json>'` | Keyword search over TikTok videos |
| `sellerclaw research-social post-youtube-trending-shorts -b '<json>'` | Trending YouTube Shorts |
| `sellerclaw research-social post-reddit-search -b '<json>'` | Reddit-wide search |
| `sellerclaw research-social post-reddit-subreddit -b '<json>'` | Recent posts in a subreddit |

Body schemas: `sellerclaw describe <op_id>` (discover via `sellerclaw list-operations --tag research-social`).

## Workflow

1. Start with `post-tiktok-popular-hashtags` or `post-tiktok-search` using seed keywords for the niche.
2. Drill into promising videos; note engagement proxies (views, likes) from `response`.
3. Cross-check with `post-youtube-trending-shorts` for Shorts momentum.
4. Use `post-reddit-search` / `post-reddit-subreddit` for pain points and language customers use.
5. Summarize: themes, rising formats, risks (fad vs sustained interest).

## Scope limits by effort

Read the effort level from the Agent Task instructions (`Effort: QUICK/STANDARD/DEEP`). If not stated, use Standard.

| Limit | Quick | Standard | Deep |
|-------|-------|----------|------|
| SociaVault calls | 0-1 | 2-3 | 5-8 |
| Social platforms checked | 0 | 1-2 (TikTok + Reddit) | 3-4 (TikTok + Reddit + YouTube + TikTok Shop) |
| Browser social visits | 0 | 0 | 1-2 (TikTok hashtag, Reddit subreddit) |

## Fallback when SociaVault is unavailable

If a CLI call exits with `503` or `research_social` is not configured:

1. `web_search`: "{niche} tiktok trending 2026" — articles about TikTok trends.
2. `web_search`: "{product} viral tiktok" — find if product has social momentum.
3. `web_search`: "site:reddit.com {niche}" — Reddit discussions indexed by Google.
4. `web_search`: "{niche} social media trending product" — social commerce coverage.
5. Browser: visit TikTok (search hashtag), Reddit (search subreddit).

Return `tiktok_engagement` and `reddit_mentions` as `"unavailable"` if no signal found via fallbacks. Do not fabricate social data.

When using web search fallbacks, note `"web_search"` in `data_sources_used` and list SociaVault as unavailable in `data_gaps`.

## Guardrails

- Prefer a small number of CLI calls per task; batch hypotheses before calling.
- Credit cost scales with `credits_usd` / vendor usage — avoid redundant pagination.
- Never present scraped personal data as identifiable; summarize at theme level.
