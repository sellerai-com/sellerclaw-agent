---
name: campaign-playbook
description: "Provider-agnostic ad-campaign playbook for Meta and Google Ads: intake (what context to gather before doing anything), creative-asset preparation, campaign-type decision tree, the standard launch flow, the periodic optimization cycle (kill / scale / fatigue / saturation rules), A/B testing, scaling cadence, and emergency rules. Use whenever the task is 'launch a campaign', 'recreate this campaign', 'run an optimization pass', 'which ads should I kill or scale', 'set up an A/B test', 'CPA is too high', 'we hit the spend cap', or any HOW question about ad decisions — paired with `facebook-ads-api` or `google-ads-api` for the API calls and `catalog` / `sales-channels` for product / store context."
---

# Campaign playbook

Provider-agnostic. Substitute `<provider>` with `facebook-ads` or `google-ads`.

## 0. Intake (run before any create / recreate)

A launch that fails because data was missing is **your failure** — gather it instead of stopping. Treat "recreate this campaign with budget X" as a **new launch**, not a literal copy: the prior campaign may be `REMOVED` / `DELETED` and its assets are not recoverable.

Mandatory inputs before you start a launch plan:

1. **Account + strategy** — `agent-ad-accounts list-for` → pick account; `agent-ad-accounts get-ad-strategy-for <id>` → read `target_cpa`, `target_roas`, `max_daily_budget`, `max_weekly_ad_spend`.
2. **Product / offer** — what is being advertised. If the task references a product or store but no ID, ask the supervisor; otherwise pull via `agent-products get <id>` (name, description, images, price, status) or `stores list-listings <store_id>` (live URL, title, image).
3. **Store / brand context** — `agent-sales-channels list-for-user` → get the storefront `name` (use as business name) and `domain` (use as final URL root). One active store ⇒ use it; multiple ⇒ ask supervisor which.
4. **Goal + budget** — explicit conversion goal (purchase / lead / traffic) and daily budget. Validate budget vs. **viability floors** (next section); reject silently-impossible budgets up front rather than after a failed create.

If 1–3 cannot be resolved, return `status: blocked` to the supervisor with the **specific** missing piece (e.g. "no product image — needs at least 1 marketing image 1200×1200"). Never give a generic "I need more data" answer.

### Budget viability floors

| Provider | Daily budget red flag | Reason |
|---|---|---|
| Google Shopping | `< $5` | Won't accumulate enough clicks to learn |
| Google Performance Max | `< 3 × target_cpa` (and ≥ `$10`) | PMax needs ~30 conv/30d to exit learning |
| Meta CONVERSIONS | `< $10` | Below CBO learning threshold |
| Meta TRAFFIC | `< $5` | Below practical CPC × 50 clicks/day |

If user-supplied budget is below the floor, surface this in the launch plan with the specific minimum required, do **not** silently accept it. Sub-cent / fractional-cent budgets always fail Google Ads API (minimum is 0.01 in account currency).

## 1. Track multi-step work as a goal

If the task is non-trivial (launch, full optimization pass, A/B set-up, recreation) and the supervisor passed an `agent_task_id`:

```bash
sellerclaw agent-goals start-task <task_id>
sellerclaw agent-goals add-progress-note <task_id> -b '{"message":"intake done: product=…, store=…, budget=…"}'
# … work …
sellerclaw agent-goals add-progress-note <task_id> -b '{"message":"assets prepared: 5 headlines, 3 descriptions, 2 images"}'
sellerclaw agent-goals request-task-review <task_id> -b '{"outcome":"created PAUSED campaign id=… with N adsets; needs activation approval"}'
```

If no `agent_task_id` was passed and the work is >3 CLI calls, ask the supervisor to create one — the owner will want to see progress.

## 2. Creative-asset preparation

For Google PMax and any Meta ad, you build the creative yourself from product + store context. Rules:

- **Pull source material** from `agent-products get` (description, images) and store/listing data.
- **Headlines / titles** — short, benefit-led, no all-caps, no "Buy now!". Write 5+ unique variants per ad set / asset group.
- **Descriptions** — concrete benefit + offer/CTA. Write 3+ variants.
- **Images** — must be hosted at an HTTPS URL Google/Meta can fetch. Source order:
  1. Product images from `agent-products get` if aspect ratio + min size match the platform spec (square 1:1 ≥1080px, landscape 1.91:1 ≥1200×628, portrait 4:5 ≥1080×1350).
  2. Existing platform creatives via `facebook-ads list-creatives` when refreshing an existing winner.
  3. **Generate** when nothing on file matches — use the `image_generate` tool with a prompt grounded in the product (name, key benefit, color, style) and the required aspect ratio. Generate one asset per required ratio rather than one and crop.
  4. For any non-HTTPS source (generated file, local path, supplier-side URL), mint a hosted URL via `sellerclaw agent-files from-url --url <…>` or `sellerclaw agent-files upload <local_path>` and pass the returned `download_url`.
- **Logo** — store/brand logo if it exists; otherwise generate a simple square logo with `image_generate` ("minimal square logo on white, brand name <name>") and host it.
- **Video** (Meta video ads, optional PMax asset) — generate with `video_generate` when catalog has no usable footage; keep clips ≤15s for Meta feed and ≤30s for PMax YouTube placement.
- **Business name** — store `name` from `sales-channels` (≤25 chars; truncate cleanly).
- **Final URL / link URL** — listing URL if present; otherwise store domain root.

Provider-specific minimums and character limits live in `google-ads-api` (PMax asset_group section) and `facebook-ads-api` (creative section) — read them before assembling the payload.

### Image-generation prompt template

Keep prompts grounded so the asset looks like the actual product, not a stock photo of the category:

```
A high-quality <aspect> product photograph of <product name>.
<one concrete visual detail from product description — color, material, key feature>.
Clean studio background, soft natural lighting, e-commerce style, no text overlay.
```

Substitute `<aspect>` with `square 1:1`, `landscape 1.91:1`, or `portrait 4:5` depending on the required asset slot. Never embed promotional copy ("50% OFF") in the generated image — Google rejects images with high text density and Meta penalises CTR. Copy lives in the headline/body fields.

## 3. Campaign-type decision tree

| Situation | Pick | Why |
|---|---|---|
| Connected Shopify/eBay + Google Merchant Center synced + budget ≥ $5/day | Google **SHOPPING** | Cheapest learning path; product feed does the targeting |
| No Merchant Center, want Google scale, budget ≥ $10/day, can produce 5 headlines + 3 desc + 2 images | Google **PERFORMANCE_MAX** | Only other supported Google type in this CLI |
| Need keyword-level Search control | **Not supported** by this CLI's `create-campaign` — return a blocker; user must set up in Google Ads UI |
| Want Meta with purchase pixel + budget ≥ $10/day | Meta **CONVERSIONS** | Optimizes for purchases |
| Meta but no pixel events configured | Meta **TRAFFIC** | CONVERSIONS will be stuck in learning forever without pixel signal |
| Existing product catalog feed on Meta | Meta **CATALOG_SALES** | Dynamic product ads |

## 4. Launch flow (provider-agnostic)

1. **Intake** (section 0). Confirm budget viable.
2. **Decide type** (section 3).
3. **Prepare assets** (section 2).
4. **Draft plan** — campaign name, type, daily_budget, bidding strategy, asset summary (counts), targeting summary. Return to supervisor for approval.
5. **On approval, create PAUSED** — `<provider> create-campaign -b '<json>'` then `create-adset` / `create-group` / asset-group attached. Server forces PAUSED on Google; on Meta, always pass `"status":"PAUSED"`.
6. **Verify** — `get-campaign <id>` shows expected fields; no `warning` blocking activation.
7. **Activate only after explicit supervisor approval** — `patch-campaign <id> -b '{"status":"ACTIVE"}'` (Meta) or `'{"status":"ENABLED"}'` (Google).

## 5. Optimization cycle

Run when supervisor delegates "optimize" / "weekly pass" / "what's underperforming":

1. List actives + metrics:
   - `<provider> get-campaigns --status ACTIVE` / `--status ENABLED`
   - `<provider> get-metrics --level adset` (Meta) / `--level ad_group` (Google) with `--date-from / --date-to` covering the strategy's `learning_period_days` (default 7).
2. Per ad set / ad group, evaluate vs. **strategy thresholds** (from intake step 1):
   - **Kill** if `cpa > target_cpa AND spend ≥ min_spend_before_kill` for 2+ consecutive days.
   - **Scale +20%** if `roas ≥ target_roas AND cpa ≤ target_cpa` for 3+ consecutive days **and** no `update_budget` action in `get-action-log --entity-id <id> --days 3`.
   - **Fatigue** (Meta only) — `frequency > 3 AND ctr` falling 3 days → recommend creative refresh.
   - **Saturation** — `cpm` up ≥ 20% WoW with flat conversions → recommend audience expansion / new asset group.
3. Return prioritized action list with concrete numbers and the proposed patch payloads. Execute on approval; record progress note per action.

## 6. A/B testing

One variable per test: creative, copy, audience, or bidding. Same budget on both arms. Target ~100 conversions per arm or 7 days — whichever first.

- **Meta:** `facebook-ads duplicate-adset <adset_id> -b '{"name":"…- B","daily_budget":X}'` then `facebook-ads create -b '<creative-variant>'` attached to the new adset.
- **Google:** no native duplicate. Recreate via `google-ads create-group` (for keyword-level variants) or a second asset group on PMax (via the proxy — patch the new asset group's assets).

Winner = lower CPA or higher ROAS over the test window. Pause the loser.

## 7. Scaling cadence

Days 1–3 observe (no changes). Day 4: `+20%` budget if `roas ≥ target_roas`. Day 7: another `+20%` if still healthy. Day 10: `+20%` or duplicate audience. Day 14+: expect plateau, plan creative refresh.

Hard rules:
- Max budget delta **per call ±20%** (server-enforced on Google; agent-enforced on Meta).
- Min 3 days between consecutive scale steps on the same entity (check via `get-action-log`).
- Never scale during weekend if Mon–Fri ROAS data only — wait for representative window.

## 8. Emergency rules (override the cycle, execute immediately)

1. **CPA blow-up** — if every active ad set / ad group in a campaign has `cpa > emergency_cpa_multiplier × target_cpa` for 2+ consecutive days, pause the campaign:
   - Meta: `facebook-ads patch-campaign <id> -b '{"status":"PAUSED"}'`
   - Google: `google-ads patch-campaign <id> -b '{"status":"PAUSED"}'`
   - Then notify supervisor with the offending entity IDs and recent CPA series.
2. **Weekly spend cap** — sum 7-day spend across active accounts; if ≥ 90% of `max_weekly_ad_spend`, **stop all scaling**. If exceeded, pause lowest-ROAS ad sets to bring weekly spend back under cap.
3. **Token expired** — any CLI call returning exit code `3` ⇒ mark account as `TOKEN_EXPIRED` in the result envelope and notify supervisor immediately; do not retry.
4. **Account disabled / billing failure** — non-3 auth-like error referencing account status ⇒ same envelope, notify, stop.
