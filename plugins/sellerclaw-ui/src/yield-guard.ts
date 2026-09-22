import type { OpenClawPluginApi } from "openclaw/plugin-sdk/core";

import { extractTargetFromSessionKey } from "./channel.js";
import { logDelivery } from "./log.js";
import { getSharedState } from "./shared-state.js";

/**
 * Stops a chat run from waiting on work it did not start.
 *
 * ``sessions_yield`` only waits on subagents spawned by the same run: each child records the run
 * that spawned it, and once that run ends the child reports back on its own — through a settle wake
 * if that run yielded, through a completion run if it did not (OpenClaw
 * ``subagent-registry-requester-yield.ts``). In any later run the tool fails, with an error worded
 * for subagents ("…must explicitly set waitFor: 'message'"). The engine's own prompt still tells
 * the supervisor to "wait with ``sessions_yield``" for running children, so it reaches for the
 * tool whenever the owner asks how the work is going — and what follows the failure is the model
 * reasoning about turns: a retry with the subagent parameter, a closing note to itself that reaches
 * the owner as a message ("I'll end this turn and the completion event will wake me"), or silence.
 *
 * Blocked here, the call costs the same one round the failed call did, and the reason the model
 * reads says what to do instead. Only our chat sessions are guarded: a subagent's session may
 * legitimately wait for a message it has not spawned anything for.
 */

/** What the model reads instead of the engine's subagent-worded error. */
export const NOTHING_TO_WAIT_FOR =
  "No subagent was spawned in this turn, so there is nothing to wait for — work started " +
  "earlier reports back to you on its own. If a `message` send in this turn already answered " +
  "the owner, end the turn with NO_REPLY alone; otherwise answer them now.";

/** Tools that start a subagent the same run may then wait on. */
const SPAWN_TOOL_NAMES = new Set(["sessions_spawn"]);
const YIELD_TOOL_NAME = "sessions_yield";

/** A run's spawn is remembered for as long as that run could still be going. */
const SPAWNING_RUN_TTL_MS = 60 * 60_000;

/** Runs that spawned a subagent, by when; process-wide for the reason in ``shared-state.ts``. */
const spawningRuns = getSharedState("yield-guard:spawning-runs", () => new Map<string, number>());

interface BeforeToolCallEvent {
  toolName?: unknown;
  runId?: unknown;
}

interface HookContext {
  sessionKey?: unknown;
  runId?: unknown;
}

function asString(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

/**
 * The block for a ``sessions_yield`` call with nothing to wait on, or ``undefined`` to let the call
 * through. Also where spawns are recorded, since they cross the same hook first.
 */
export function yieldGuardDecision(
  event: BeforeToolCallEvent,
  ctx?: HookContext,
): { block: true; blockReason: string } | undefined {
  const toolName = asString(event?.toolName);
  const runId = asString(event?.runId) || asString(ctx?.runId);
  const now = Date.now();
  if (SPAWN_TOOL_NAMES.has(toolName)) {
    if (!runId) return undefined;
    for (const [run, at] of spawningRuns) {
      if (now - at > SPAWNING_RUN_TTL_MS) spawningRuns.delete(run);
    }
    spawningRuns.set(runId, now);
    return undefined;
  }
  if (toolName !== YIELD_TOOL_NAME) return undefined;
  const sessionKey = asString(ctx?.sessionKey);
  // Only our chats; and a call no run can be attributed to is left to the engine to judge.
  if (!sessionKey || !extractTargetFromSessionKey(sessionKey) || !runId) return undefined;
  if (spawningRuns.has(runId)) return undefined;
  return { block: true, blockReason: NOTHING_TO_WAIT_FOR };
}

export function registerYieldGuard(api: OpenClawPluginApi): void {
  if (typeof api.on !== "function") return;
  api.on("before_tool_call", (event: BeforeToolCallEvent, ctx?: HookContext) => {
    const decision = yieldGuardDecision(event, ctx);
    if (decision) {
      logDelivery(
        api,
        `sessions_yield blocked: nothing spawned in this run ` +
          `run_id=${asString(event?.runId) || asString(ctx?.runId)} ` +
          `session_key=${asString(ctx?.sessionKey)}`,
      );
    }
    return decision;
  });
}

/** Test-only: forget every recorded spawn. */
export function __resetYieldGuardState(): void {
  spawningRuns.clear();
}
