import type { OpenClawPluginApi } from "openclaw/plugin-sdk/core";

import { resolveSellerclawUiAccount, type ScwUiAccount } from "./channel.js";
import { logInfo, logWarn } from "./log.js";
import { getSharedState } from "./shared-state.js";

/**
 * Puts the owner's active goal in front of the coordinator's turns.
 *
 * Before each turn of the supervisor — in a chat on any channel, in a scheduled run, in a run the
 * cloud started — the cloud is asked for the `<active_goal>` block (the goal, the agent's working
 * state for it, what waits on the owner) and the block is prepended to the turn.
 *
 * OpenClaw keeps prepended context in the session's history (the turn's model-facing text is
 * frozen into the transcript and replayed on later turns), so the block is added only when it
 * differs from the one this session got last: on its first turn, after the history is compacted
 * or reset, and whenever the goal, the state or what waits on the owner changes. Adding it on
 * every turn would stack a copy per turn into the history.
 *
 * A failed or slow fetch never holds a turn up: it is logged and the turn goes without the block.
 */

/** How long one cloud answer serves every session of this agent. */
export const BRIEF_CACHE_TTL_MS = 20_000;
/** The cloud read gets this long; a turn's prompt hooks get 15 s in total. */
export const BRIEF_FETCH_TIMEOUT_MS = 3_000;
/** Sessions remembered at most; the oldest is forgotten first. */
export const MAX_TRACKED_SESSIONS = 500;

/** Said once to a session whose history still carries a block for a goal no longer in play. */
export const GOAL_GONE_TEXT =
  "<active_goal>The owner no longer has a goal in play; the earlier <active_goal> block is out of date.</active_goal>";

const SUPERVISOR_AGENT_ID = "supervisor";
const AGENT_ID_RE = /^agent:([^:]+):/i;
/** What a session was last given: a brief's fingerprint, or `NO_GOAL` after the goal-gone line. */
const NO_GOAL = "";

interface Brief {
  text: string;
  fingerprint: string;
}

interface CachedBrief {
  fetchedAt: number;
  brief: Brief | null;
}

interface BriefCache {
  entry: CachedBrief | null;
  pending: Promise<Brief | null> | null;
}

const cache = getSharedState<BriefCache>("goal-brief:cache", () => ({ entry: null, pending: null }));
const lastGivenBySession = getSharedState("goal-brief:last-given", () => new Map<string, string>());

interface HookContext {
  sessionKey?: unknown;
  agentId?: unknown;
  hookInvocation?: { assertActive?: () => void };
}

function asString(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

/** Whether a turn in this session is the coordinator's own — not a specialist's. */
export function isCoordinatorSession(sessionKey: string, agentId: string): boolean {
  if (!sessionKey || sessionKey.includes(":subagent:")) return false;
  const owner = agentId || AGENT_ID_RE.exec(sessionKey)?.[1] || "";
  return owner.toLowerCase() === SUPERVISOR_AGENT_ID;
}

async function fetchBrief(account: ScwUiAccount): Promise<Brief | null> {
  const url = `${account.apiBaseUrl.replace(/\/$/, "")}/agent/goals/brief`;
  const res = await fetch(url, {
    method: "GET",
    headers: { Authorization: `Bearer ${account.agentApiKey}` },
    signal: AbortSignal.timeout(BRIEF_FETCH_TIMEOUT_MS),
  });
  if (!res.ok) {
    throw new Error(`goal brief HTTP ${res.status}`);
  }
  const body = (await res.json()) as { text?: unknown; fingerprint?: unknown } | null;
  const text = typeof body?.text === "string" ? body.text : "";
  const fingerprint = typeof body?.fingerprint === "string" ? body.fingerprint : "";
  return text && fingerprint ? { text, fingerprint } : null;
}

/** The current brief, from the cache while it is fresh; concurrent turns share one read. */
async function currentBrief(account: ScwUiAccount): Promise<Brief | null> {
  const entry = cache.entry;
  if (entry && Date.now() - entry.fetchedAt < BRIEF_CACHE_TTL_MS) {
    return entry.brief;
  }
  if (!cache.pending) {
    cache.pending = fetchBrief(account)
      .then((brief) => {
        cache.entry = { fetchedAt: Date.now(), brief };
        return brief;
      })
      .finally(() => {
        cache.pending = null;
      });
  }
  return cache.pending;
}

function remember(sessionKey: string, given: string): void {
  lastGivenBySession.delete(sessionKey);
  lastGivenBySession.set(sessionKey, given);
  while (lastGivenBySession.size > MAX_TRACKED_SESSIONS) {
    const oldest = lastGivenBySession.keys().next().value;
    if (oldest === undefined) break;
    lastGivenBySession.delete(oldest);
  }
}

/** Forget what a session was given, so its next turn gets the block again. */
function forget(_event: unknown, ctx?: HookContext): undefined {
  const sessionKey = asString(ctx?.sessionKey);
  if (sessionKey) lastGivenBySession.delete(sessionKey);
  return undefined;
}

export function registerGoalBrief(api: OpenClawPluginApi): void {
  if (typeof api.on !== "function") {
    logWarn(api, "sellerclaw-ui: goal brief not installed (api.on unavailable)");
    return;
  }
  api.on(
    "before_prompt_build",
    async (_event: unknown, ctx?: HookContext): Promise<{ prependContext: string } | undefined> => {
      const sessionKey = asString(ctx?.sessionKey);
      if (!isCoordinatorSession(sessionKey, asString(ctx?.agentId))) return undefined;
      let brief: Brief | null;
      try {
        brief = await currentBrief(resolveSellerclawUiAccount(api.config));
      } catch (err) {
        logWarn(api, `sellerclaw-ui: goal brief unavailable for ${sessionKey}: ${String(err)}`);
        return undefined;
      }
      const lastGiven = lastGivenBySession.get(sessionKey);
      let text: string;
      let given: string;
      if (brief) {
        if (lastGiven === brief.fingerprint) return undefined;
        text = brief.text;
        given = brief.fingerprint;
      } else {
        if (lastGiven === undefined || lastGiven === NO_GOAL) return undefined;
        text = GOAL_GONE_TEXT;
        given = NO_GOAL;
      }
      try {
        ctx?.hookInvocation?.assertActive?.();
      } catch {
        return undefined;
      }
      remember(sessionKey, given);
      logInfo(api, `sellerclaw-ui: goal brief added to ${sessionKey}`);
      return { prependContext: text };
    },
  );
  api.on("after_compaction", forget);
  api.on("before_reset", forget);
  api.on("session_end", forget);
  logInfo(api, "sellerclaw-ui: goal brief installed");
}

/** Test-only: drop the cached brief and every session's record. */
export function __resetGoalBrief(): void {
  cache.entry = null;
  cache.pending = null;
  lastGivenBySession.clear();
}
