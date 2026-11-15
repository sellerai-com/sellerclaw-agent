import type { OpenClawPluginApi } from "openclaw/plugin-sdk/core";

import { resolveSellerclawUiAccount } from "./channel.js";
import { lastUserMessageText } from "./completion-run.js";
import { logError, logInfo, logWarn } from "./log.js";
import { postSubagentRunEnded } from "./send.js";

/**
 * Tells the cloud when the supervisor's turn about a specialist's result has ended.
 *
 * When a specialist finishes, OpenClaw wakes the supervisor for one turn with the result. On a
 * Claude subscription brain (`claude-cli`) and a chat, that turn is a relay: OpenClaw hands it the
 * `message` tool and nothing else — deliberately (openclaw#198584) — so the supervisor tells the
 * owner the result but cannot run the command that accepts it or closes the job. The job then sat
 * "in progress" behind a report the owner had already read, until the cloud's watchdog woke the
 * supervisor five to fifteen minutes later in an ordinary turn, where all its tools work.
 *
 * Reporting the turn's end lets the cloud do that at once. The cloud decides whether it is needed
 * and stays silent otherwise: the job already closed (the usual case on an API brain, whose relay
 * keeps its tools), the result already accepted, another specialist under the job still working,
 * a blocking question to the owner open, or a reminder already sent. The reminder lands in the
 * same chat session, so OpenClaw runs it after this turn, never beside it.
 *
 * Only `announce:v1:` runs are reported. A `sessions_yield` continuation
 * (`announce:requester-settle:…`) keeps the supervisor's tools, and its run id names no specialist.
 *
 * The report waits {@link COMPLETION_TURN_REPORT_DELAY_MS} after the turn ends. The cloud wakes a
 * chat session only while it is idle, and it learns that from the agent's ping (~every 10 s): a
 * report sent the moment the turn ended found the session still listed as running, so the
 * reminder sat until the next digest sweep three minutes later (live check, 15.11.2026). Two pings'
 * worth of waiting lets the cloud see the session free.
 */

/** How long after the turn ends the report goes out: two of the agent's ~10 s pings. */
export const COMPLETION_TURN_REPORT_DELAY_MS = 20_000;

/** Run id of a completion turn: `announce:v1:<specialist session>:<specialist run id>`. */
const ANNOUNCE_V1_PREFIX = "announce:v1:";

/** A specialist's session: `agent:<agentId>:subagent:<id>`. */
const SUBAGENT_SESSION_RE = /^agent:[^:\s]+:subagent:[^:\s]+/;

/** Fallback when a runtime stops naming the specialist in the run id: the trigger names it. */
const TRIGGER_SOURCE_RE = /sourceSession=(agent:[^:\s]+:subagent:[^:\s]+)/;

function asString(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

/**
 * The specialist session whose result this turn was about, or `""` when it was not a completion
 * turn. Read from the run id first; the trigger prompt is a fallback, and only when it says it
 * came from a subagent announcement — a pasted trigger in an owner's message must not count.
 */
export function completionTurnChildSessionKey(runId: unknown, messages: unknown): string {
  const id = asString(runId);
  if (id.startsWith(ANNOUNCE_V1_PREFIX)) {
    return SUBAGENT_SESSION_RE.exec(id.slice(ANNOUNCE_V1_PREFIX.length))?.[0] ?? "";
  }
  if (id) return "";
  const trigger = lastUserMessageText(messages);
  if (!trigger.includes("sourceTool=subagent_announce")) return "";
  return TRIGGER_SOURCE_RE.exec(trigger)?.[1] ?? "";
}

interface AgentEndEvent {
  runId?: unknown;
  messages?: unknown;
}

interface HookContext {
  sessionKey?: unknown;
  runId?: unknown;
}

/**
 * Subscribe to `agent_end`. Fire-and-forget by contract: the report is posted in the background
 * and a failure is logged, never awaited by the engine. The cloud's watchdog still covers anything
 * that does not get through, only later.
 */
export function registerCompletionTurnHandinReport(api: OpenClawPluginApi): void {
  if (typeof api.on !== "function") {
    logWarn(api, "sellerclaw-ui: completion-turn report not installed (api.on unavailable)");
    return;
  }
  api.on("agent_end", (event: AgentEndEvent, ctx?: HookContext): undefined => {
    const runId = asString(event?.runId) || asString(ctx?.runId);
    const childSessionKey = completionTurnChildSessionKey(runId, event?.messages);
    if (!childSessionKey) return undefined;
    const requesterSessionKey = asString(ctx?.sessionKey);
    if (!requesterSessionKey) return undefined;
    let account;
    try {
      account = resolveSellerclawUiAccount(api.config);
    } catch (err) {
      logError(api, `sellerclaw-ui: completion-turn report cannot resolve account: ${String(err)}`);
      return undefined;
    }
    const report = (): void => {
      void postSubagentRunEnded(account, {
        childSessionKey,
        requesterSessionKey,
        runId,
        trigger: "completion_turn",
      })
        .then(() => {
          logInfo(
            api,
            `sellerclaw-ui: reported completion turn for ${childSessionKey} in ${requesterSessionKey}`,
          );
        })
        .catch((err: unknown) => {
          logError(
            api,
            `sellerclaw-ui: completion-turn report failed run=${runId || "?"} ` +
              `child=${childSessionKey}: ${String(err)}`,
          );
        });
    };
    // A pending report must never keep the gateway process alive on shutdown: the cloud's
    // watchdog covers a report that never went out.
    const timer = setTimeout(report, COMPLETION_TURN_REPORT_DELAY_MS);
    (timer as { unref?: () => void }).unref?.();
    return undefined;
  });
  logInfo(api, "sellerclaw-ui: completion-turn report installed");
}
