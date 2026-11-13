import { readFileSync, statSync } from "node:fs";

import type { OpenClawPluginApi } from "openclaw/plugin-sdk/core";

import { currentPluginConfig } from "./channel.js";
import { logInfo } from "./log.js";
import { getSharedState } from "./shared-state.js";

/**
 * Holds the agent while the owner's own AI plan cannot serve it.
 *
 * On a self-hosted agent the owner may run the team on their own Claude or ChatGPT plan. When the
 * plan's usage limit is used up — or the agent lost its sign-in — the agent WAITS: it never falls
 * back to SellerClaw's paid models (owner's decision, 2026-11-12). The agent server watches the plan
 * and writes what it sees to ``status.json``; this gate reads it before every turn
 * (``before_agent_reply``, which claims a turn before any model call) and, while the agent waits:
 *
 *  - answers the owner's message with one short notice — why, and until when;
 *  - skips a scheduled run with that reason as its summary, reported as ``skipped`` (a run claimed
 *    here is never a failure, so a long limit cannot pile up the failures that make OpenClaw switch
 *    a task off on its own);
 *  - keeps heartbeats and subagent-completion announcements silent, so the owner is told once.
 *
 * The first limit hit lands mid-turn; ``agent_end`` then nudges the agent server to look again at
 * once, so the very next turn is held instead of failing the same way.
 */

export interface AiSubscriptionSettings {
  provider: string;
  statusPath: string;
}

export interface AiSubscriptionStatus {
  state: string;
  resumesAt: number | null;
}

export interface GateDecision {
  /** Claim the turn (no model call). */
  claim: boolean;
  /** The claiming reply; ``undefined`` claims silently. */
  text?: string;
  /** For a scheduled run: the reason it was skipped. */
  skipReason?: string;
}

const PROVIDER_NAMES: Record<string, string> = { claude: "Claude", chatgpt: "ChatGPT" };
const ANNOUNCE_RUN_ID_PREFIX = "announce:";
const SKIPPED_RUN_TTL_MS = 60 * 60_000;

/** Runs this gate skipped, by run id → reason; read when the cron run is reported to the cloud. */
const skippedRuns = getSharedState(
  "subscription-gate:skipped-runs",
  () => new Map<string, { reason: string; expiresAt: number }>(),
);
/** ``status.json`` as last read, keyed by mtime so a busy chat does not re-read it every turn. */
const statusCache = getSharedState("subscription-gate:status-cache", () => ({
  path: "",
  mtimeMs: -1,
  status: null as AiSubscriptionStatus | null,
}));

function asString(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** The ``aiSubscription`` block the bundle writes into this plugin's config, if any. */
export function readAiSubscriptionSettings(cfg: unknown = currentPluginConfig()): AiSubscriptionSettings | null {
  if (!isRecord(cfg) || !isRecord(cfg.channels)) return null;
  const section = cfg.channels["sellerclaw-ui"];
  if (!isRecord(section) || !isRecord(section.aiSubscription)) return null;
  const provider = asString(section.aiSubscription.provider).trim();
  const statusPath = asString(section.aiSubscription.statusPath).trim();
  return provider && statusPath ? { provider, statusPath } : null;
}

export function parseStatus(raw: string): AiSubscriptionStatus | null {
  try {
    const data: unknown = JSON.parse(raw);
    if (!isRecord(data)) return null;
    const resumes = Date.parse(asString(data.resumes_at));
    return { state: asString(data.state), resumesAt: Number.isFinite(resumes) ? resumes : null };
  } catch {
    return null;
  }
}

/** The status the agent server last wrote, or ``null`` when there is none (nothing to hold on). */
export function readStatus(path: string): AiSubscriptionStatus | null {
  let mtimeMs: number;
  try {
    mtimeMs = statSync(path).mtimeMs;
  } catch {
    return null;
  }
  if (statusCache.path === path && statusCache.mtimeMs === mtimeMs) return statusCache.status;
  let status: AiSubscriptionStatus | null = null;
  try {
    status = parseStatus(readFileSync(path, "utf8"));
  } catch {
    status = null;
  }
  statusCache.path = path;
  statusCache.mtimeMs = mtimeMs;
  statusCache.status = status;
  return status;
}

/** "in about 2 hours" — a wait in words that needs no time zone. */
export function inAbout(ms: number): string {
  const minutes = Math.max(1, Math.round(ms / 60_000));
  if (minutes < 60) return `in about ${minutes} minute${minutes === 1 ? "" : "s"}`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `in about ${hours} hour${hours === 1 ? "" : "s"}`;
  return `in about ${Math.round(hours / 24)} days`;
}

export function gateDecision(params: {
  settings: AiSubscriptionSettings;
  status: AiSubscriptionStatus | null;
  trigger: string;
  runId: string;
  now: number;
}): GateDecision {
  const { settings, status, trigger, runId, now } = params;
  if (status === null) return { claim: false };
  const name = PROVIDER_NAMES[settings.provider] ?? settings.provider;
  let notice: string;
  let reason: string;
  if (status.state === "limit_reached") {
    if (status.resumesAt !== null && status.resumesAt <= now) {
      // The window already reset; let the turn through — the plan will say if it still refuses.
      return { claim: false };
    }
    const when = status.resumesAt !== null ? ` — ${inAbout(status.resumesAt - now)}` : "";
    notice =
      `Your ${name} plan's usage limit is used up, so I'm paused until it resets${when}. ` +
      "Please send your message again then. Nothing is charged to your SellerClaw credits.";
    reason = `your ${name} plan's usage limit is used up (it resets${when ? when.replace(" — ", " ") : " soon"})`;
  } else if (status.state === "signed_out") {
    const command = `sellerclaw-agent connect ${settings.provider}`;
    notice =
      `I've lost the sign-in to your ${name} plan, so I can't work right now. To reconnect, run ` +
      `\`${command}\` on the computer running your agent.`;
    reason = `the agent lost its ${name} sign-in (run ${command} to reconnect)`;
  } else {
    return { claim: false };
  }
  if (trigger === "cron") {
    const skipReason = `Skipped: ${reason}. This task runs again at its next scheduled time.`;
    return { claim: true, text: skipReason, skipReason };
  }
  if (trigger === "heartbeat" || runId.startsWith(ANNOUNCE_RUN_ID_PREFIX)) {
    return { claim: true };
  }
  return { claim: true, text: notice };
}

/** The reason a run was skipped by this gate, consumed once (when its cron run is reported). */
export function takeSkippedRun(runId: string, now: number = Date.now()): string | null {
  for (const [id, entry] of skippedRuns) {
    if (entry.expiresAt <= now) skippedRuns.delete(id);
  }
  const entry = skippedRuns.get(runId);
  if (!entry) return null;
  skippedRuns.delete(runId);
  return entry.reason;
}

interface ReplyContext {
  trigger?: unknown;
  runId?: unknown;
}

/** A failed run whose error says the plan refused (limit, sign-in): worth an immediate look. */
const PLAN_REFUSAL_PATTERN =
  /usage limit|rate[ _-]?limit|quota|limit reached|resets? (at|in)|unauthori[sz]ed|authentication|not logged in|log ?in again|oauth/i;

function nudgeAgentServer(api: OpenClawPluginApi): void {
  const cfg = currentPluginConfig() as unknown;
  if (!isRecord(cfg) || !isRecord(cfg.channels)) return;
  const section = cfg.channels["sellerclaw-ui"];
  if (!isRecord(section)) return;
  const base = asString(section.localAgentBaseUrl).trim() || "http://127.0.0.1:8001";
  const token = asString(section.internalWebhookSecret).trim();
  if (!token) return;
  void fetch(`${base.replace(/\/$/, "")}/internal/ai-subscription/refresh`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
  }).catch((err: unknown) => {
    logInfo(api, `sellerclaw-ui[subscription]: refresh nudge failed: ${String(err)}`);
  });
}

export function registerSubscriptionGate(api: OpenClawPluginApi): void {
  if (typeof api.on !== "function") return;
  api.on(
    "before_agent_reply",
    (_event: unknown, ctx?: ReplyContext) => {
      const settings = readAiSubscriptionSettings();
      if (settings === null) return undefined;
      const now = Date.now();
      const runId = asString(ctx?.runId);
      const decision = gateDecision({
        settings,
        status: readStatus(settings.statusPath),
        trigger: asString(ctx?.trigger),
        runId,
        now,
      });
      if (!decision.claim) return undefined;
      if (decision.skipReason && runId) {
        skippedRuns.set(runId, { reason: decision.skipReason, expiresAt: now + SKIPPED_RUN_TTL_MS });
      }
      return { handled: true, ...(decision.text ? { reply: { text: decision.text } } : {}) };
    },
    { eligibleTriggers: ["user", "cron", "heartbeat"] },
  );
  api.on("agent_end", (event: { success?: unknown; error?: unknown }) => {
    if (event?.success === true || readAiSubscriptionSettings() === null) return undefined;
    if (PLAN_REFUSAL_PATTERN.test(asString(event?.error))) nudgeAgentServer(api);
    return undefined;
  });
}
