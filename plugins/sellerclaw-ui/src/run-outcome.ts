import type { OpenClawPluginApi } from "openclaw/plugin-sdk/core";

import { extractTargetFromSessionKey } from "./channel.js";
import { logInfo, logWarn } from "./log.js";
import { getSharedState } from "./shared-state.js";

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
   * On the deployed runtime (``ghcr.io/openclaw/openclaw:2026.9.1-beta.1``) an abort suppresses
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

/**
 * Process-wide, not module-level: the writer and the reader live in different module instances.
 *
 * `agent_end` is registered on every plugin-registry pass (see `hook-registration.ts`) and so
 * fires from the newest evaluation of this module, while the inbound turn that reads the verdict
 * (`readRunOutcome` / `consumeOwnerAbort` in `inbound.ts`) runs inside the HTTP route registered
 * on the first pass. Module-local maps would put the two on opposite sides of that split and the
 * turn would never see how its run ended. See `shared-state.ts`.
 */
const runOutcomes = getSharedState(
  "run-outcome:outcomes",
  () => new Map<string, StoredRunOutcome>(),
);
const ownerAborts = getSharedState("run-outcome:owner-aborts", () => new Map<string, number>());
const continuations = getSharedState(
  "run-outcome:continuations",
  () => new Map<string, ContinuationState>(),
);

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
  if (typeof api.on !== "function") {
    // Without this subscription every failed turn reads as "no verdict" and takes the
    // conservative no-recovery branch, so an interrupted turn dies where it could have
    // resumed. Silent degradation is the worst outcome here — say it out loud.
    logWarn(api, "sellerclaw-ui: run outcome tracker not installed (api.on unavailable)");
    return;
  }
  logInfo(api, "sellerclaw-ui: run outcome tracker installed");
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

/**
 * Failures whose cause is the pipe, not the request: the model was reachable, started (or was
 * about to start) answering, and the connection died. Re-asking is the correct response — the
 * work already done is in the session, so a continuation picks up where the stream broke.
 *
 * Everything not listed here is treated as needing a human, which is the safe default: a retry
 * on a billing, auth, quota or context-size failure burns the owner's credits to reproduce the
 * same message. Ordinary tool failures never reach this classifier — the engine reports those
 * beside a real answer, and that path completes normally.
 */
const TRANSPORT_FAILURE_PATTERNS: readonly RegExp[] = [
  // Nothing came back in time, or the stream stopped mid-answer (undici reports the latter as
  // a bare "terminated"). Both are our most common real-world failure.
  /\btimed out\b/i,
  /\btimeout\b/i,
  /\bterminated\b/i,
  // The connection itself failed: DNS, refused, reset, TLS dropped mid-stream. LiteLLM wraps
  // the last one as APIConnectionError + "Response payload is not completed".
  /\bAPIConnectionError\b/i,
  /\bnetwork connection error\b/i,
  /\bconnection error\b/i,
  /\bECONNRESET\b/i,
  /\bECONNREFUSED\b/i,
  /\bEPIPE\b/i,
  /\bsocket hang up\b/i,
  /\bpayload is not completed\b/i,
  /\bpremature close\b/i,
  // A gateway in front of the model was momentarily unable to serve the request.
  /\b50[234]\b/,
  /\bbad gateway\b/i,
  /\bservice unavailable\b/i,
];

/**
 * Failures that look transport-ish by wording but must never be retried automatically: the
 * request would fail the same way, and some of them cost money or need a config change. Checked
 * first, so a message carrying both (e.g. a rate-limit body delivered over a 503) stays put.
 */
const HUMAN_NEEDED_PATTERNS: readonly RegExp[] = [
  /\brate limit/i,
  /\bquota\b/i,
  /\binsufficient (credit|balance|funds)/i,
  /\bbilling\b/i,
  /\bpayment\b/i,
  /\bauthenticat/i,
  /\bunauthorized\b/i,
  /\binvalid api key\b/i,
  /\bcontext overflow\b/i,
  /\bmodel was not found\b/i,
];

/**
 * Whether a failed turn may retry itself.
 *
 * Reads the engine's failure text because that is what this turn actually has: see
 * ``lastErrorFinalText`` in ``inbound.ts``. An empty or unrecognised message is NOT retried —
 * silence about the cause is not evidence that retrying is safe.
 */
export function isTransportTurnFailure(errorText: string): boolean {
  const text = (errorText ?? "").trim();
  if (!text) return false;
  if (HUMAN_NEEDED_PATTERNS.some((pattern) => pattern.test(text))) return false;
  return TRANSPORT_FAILURE_PATTERNS.some((pattern) => pattern.test(text));
}

/** Test-only: drop all cross-run state so cases cannot leak into each other. */
export function __resetRunOutcomeState(): void {
  runOutcomes.clear();
  ownerAborts.clear();
  continuations.clear();
}
