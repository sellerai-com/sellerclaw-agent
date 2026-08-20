import type { OpenClawPluginApi } from "openclaw/plugin-sdk/core";

import { extractTargetFromSessionKey } from "./channel.js";

/**
 * Per-session bookkeeping that lets a chat turn tell *how* it ended, so an aborted turn can
 * recover itself instead of posting the engine's error string as the assistant's answer.
 *
 * Three facts are tracked, all keyed by session key — the only identifier the run hooks, the
 * abort route and the inbound turn all share:
 *
 *  1. **Run outcome** (`agent_end`) — which failure family ended the run. Needed because
 *     `isError` on a delivered payload says "this final is an error", not "…and it is safe to
 *     retry": a billing or auth failure must reach a human, never a retry that burns credits.
 *  2. **Owner-initiated stop** — an owner pressing stop leaves the same terminal state as a
 *     budget death, and `agent_end` cannot tell them apart. We can, because we cause one of
 *     them.
 *  3. **Continuation attempts** — the bound on self-recovery. Without it a wedged session
 *     would retry forever on the owner's credits.
 */

/**
 * How long a recorded run outcome stays readable. The turn reads it in ``finishTurn``, moments
 * after the dispatch settles, so this only has to outlive one run unwind; reads also consume the
 * record (see ``readRunOutcome``), so the TTL is a backstop for a turn that never reads at all.
 */
const RUN_OUTCOME_TTL_MS = 60_000;

/**
 * How long an owner stop stays attributable to the run it aborted. Also short, and additionally
 * consumed on read — the mark must never survive into an unrelated later turn, where it would
 * silence a genuine failure.
 */
const OWNER_ABORT_TTL_MS = 60_000;

/** Window over which continuation attempts accumulate for one session. */
const CONTINUATION_TTL_MS = 30 * 60_000;

/** Hard bound on self-recovery attempts per session inside {@link CONTINUATION_TTL_MS}. */
export const MAX_CONTINUATIONS = 2;

/**
 * Announce (subagent-completion) runs share the chat's session key with ordinary chat turns —
 * see ``completion-delivery.ts``. Their ``agent_end`` must not overwrite the verdict of the turn
 * that is finishing, so they are skipped: an announce run is never the inbound turn's own run
 * (inbound runs carry a plain uuid).
 */
const ANNOUNCE_RUN_ID_PREFIX = "announce:";

export interface RunOutcome {
  /** ``false`` when the run aborted or ended on a prompt error. */
  success: boolean;
  /**
   * Whether ``agent_end`` carried an error string.
   *
   * On the deployed runtime (``ghcr.io/openclaw/openclaw:2026.8.1-beta.2``) an abort suppresses
   * this field on purpose — upstream comments it as "abort outranks failure in terminal-outcome
   * precedence" — so `success: false` with no error is the budget-timeout/abort family, while a
   * provider failure (billing, auth, rate limit) arrives with the error set. Older runtimes set
   * the field unconditionally; there the two families look alike and this reads as "error
   * family", which costs us the continuation but never produces a wrong one. That asymmetry is
   * deliberate: drift degrades self-recovery to today's behaviour, not to a retry loop.
   */
  hasError: boolean;
}

interface StoredRunOutcome extends RunOutcome {
  expiresAt: number;
}

interface ContinuationState {
  count: number;
  expiresAt: number;
}

const runOutcomes = new Map<string, StoredRunOutcome>();
const ownerAborts = new Map<string, number>();
const continuations = new Map<string, ContinuationState>();

function asString(value: unknown): string {
  return typeof value === "string" ? value : "";
}

interface AgentEndEvent {
  runId?: unknown;
  success?: unknown;
  error?: unknown;
}

interface HookContext {
  sessionKey?: unknown;
  runId?: unknown;
}

/**
 * Record how each chat run ended.
 *
 * ``agent_end`` is a conversation hook, so it needs
 * ``plugins.entries.sellerclaw-ui.hooks.allowConversationAccess`` in the OpenClaw config (the
 * bundle sets it, and ``completion-delivery.ts`` already depends on the same gate). It is
 * dispatched fire-and-forget by the runtime, so nothing may *wait* on it: the turn reads what
 * was recorded at ``finishTurn`` and treats a missing record as "cannot vouch for this run".
 */
export function registerRunOutcomeTracker(api: OpenClawPluginApi): void {
  if (typeof api.on !== "function") return;
  api.on("agent_end", (event: AgentEndEvent, ctx?: HookContext) => {
    const sessionKey = asString(ctx?.sessionKey);
    if (!sessionKey || !extractTargetFromSessionKey(sessionKey)) return undefined;
    if (asString(event?.runId ?? ctx?.runId).startsWith(ANNOUNCE_RUN_ID_PREFIX)) return undefined;
    runOutcomes.set(sessionKey, {
      success: event?.success === true,
      hasError: asString(event?.error).trim().length > 0,
      expiresAt: Date.now() + RUN_OUTCOME_TTL_MS,
    });
    return undefined;
  });
}

/**
 * The last recorded outcome for this session, or ``null`` when none is applicable.
 *
 * Read-and-clear: one run's verdict decides exactly one turn end. Without this, a verdict left
 * behind by turn N (the TTL alone keeps it up to a minute) could be read by turn N+1 whose own
 * ``agent_end`` lost the fire-and-forget race — a stale verdict is worse than none, because
 * "none" takes the conservative no-continuation branch.
 */
export function readRunOutcome(sessionKey: string): RunOutcome | null {
  const stored = runOutcomes.get(sessionKey);
  if (!stored) return null;
  runOutcomes.delete(sessionKey);
  if (stored.expiresAt <= Date.now()) return null;
  return { success: stored.success, hasError: stored.hasError };
}

/**
 * Remember that the owner stopped this session's run. Called only when a run was actually
 * aborted, so the mark cannot linger from a no-op stop and silence an unrelated later failure.
 */
export function markOwnerAbort(sessionKey: string): void {
  ownerAborts.set(sessionKey, Date.now() + OWNER_ABORT_TTL_MS);
}

/** Read-and-clear: a stop belongs to exactly one turn. */
export function consumeOwnerAbort(sessionKey: string): boolean {
  const expiresAt = ownerAborts.get(sessionKey);
  if (expiresAt === undefined) return false;
  ownerAborts.delete(sessionKey);
  return expiresAt > Date.now();
}

/**
 * Claim the next continuation slot for this session.
 *
 * Returns the attempt number (1-based) when recovery may proceed, or ``null`` once
 * {@link MAX_CONTINUATIONS} is spent — at which point the turn must surface the failure to the
 * owner instead of trying again.
 */
export function claimContinuation(sessionKey: string): number | null {
  const now = Date.now();
  const current = continuations.get(sessionKey);
  const count = current && current.expiresAt > now ? current.count : 0;
  if (count >= MAX_CONTINUATIONS) return null;
  const next = count + 1;
  continuations.set(sessionKey, { count: next, expiresAt: now + CONTINUATION_TTL_MS });
  return next;
}

/**
 * Drop the attempt counter after a turn that ended normally: recovery (if any) worked, and a
 * later unrelated abort deserves a full budget of its own.
 */
export function resetContinuations(sessionKey: string): void {
  continuations.delete(sessionKey);
}

/** Test-only: drop all cross-run state so cases cannot leak into each other. */
export function __resetRunOutcomeState(): void {
  runOutcomes.clear();
  ownerAborts.clear();
  continuations.clear();
}
