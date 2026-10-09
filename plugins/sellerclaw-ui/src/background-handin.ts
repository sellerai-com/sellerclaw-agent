import type { OpenClawPluginApi } from "openclaw/plugin-sdk/core";

import { resolveSellerclawUiAccount } from "./channel.js";
import { logError, logInfo, logWarn } from "./log.js";
import { postSubagentRunEnded } from "./send.js";

/**
 * Tells the cloud when a specialist spawned from a background supervisor run ends.
 *
 * A specialist reports back by waking the run that spawned it. A cloud wake about work with no
 * open chat (a job created from the dashboard, an errand about one) starts the supervisor as an
 * isolated cron run, and that run has usually ended by the time its specialist finishes — so the
 * engine drops the wake ("Subagent terminal signal owner changed before commit") and the
 * supervisor heard about the hand-in only when the cloud's watchdog noticed it minutes later.
 * `subagent_ended` still fires for those runs; forwarding it lets the cloud wake the supervisor
 * right away.
 *
 * A specialist spawned from a chat is left alone: the chat's session outlives its runs, and the
 * engine's own wake reaches it — reporting those too would wake the supervisor twice.
 */

/** `agent:<agentId>:cron:<jobId>:run:<runId>` — the session of one isolated cron run. */
const CRON_RUN_SESSION_RE = /^agent:[^:]+:cron:[^:]+:run:[^:]+$/i;

/** Whether a session belongs to one isolated (background) cron run, not to a lasting chat. */
export function isBackgroundRunSessionKey(sessionKey: string): boolean {
  return CRON_RUN_SESSION_RE.test(sessionKey.trim());
}

function asString(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

interface SubagentEndedEvent {
  targetSessionKey?: unknown;
  runId?: unknown;
}

interface HookContext {
  runId?: unknown;
  childSessionKey?: unknown;
  requesterSessionKey?: unknown;
}

/**
 * Subscribe to `subagent_ended`. A plain lifecycle hook — no conversation access needed — and
 * fire-and-forget by contract: the report is posted in the background and a failure is logged,
 * never awaited by the engine. The cloud's watchdog still covers anything that does not get
 * through, only later.
 */
export function registerBackgroundHandinRelay(api: OpenClawPluginApi): void {
  if (typeof api.on !== "function") {
    logWarn(api, "sellerclaw-ui: background hand-in relay not installed (api.on unavailable)");
    return;
  }
  api.on("subagent_ended", (event: SubagentEndedEvent, ctx?: HookContext): undefined => {
    const requesterSessionKey = asString(ctx?.requesterSessionKey);
    if (!isBackgroundRunSessionKey(requesterSessionKey)) return undefined;
    const childSessionKey = asString(event?.targetSessionKey) || asString(ctx?.childSessionKey);
    if (!childSessionKey) return undefined;
    const runId = asString(event?.runId) || asString(ctx?.runId);
    let account;
    try {
      account = resolveSellerclawUiAccount(api.config);
    } catch (err) {
      logError(api, `sellerclaw-ui: background hand-in relay cannot resolve account: ${String(err)}`);
      return undefined;
    }
    void postSubagentRunEnded(account, { childSessionKey, requesterSessionKey, runId })
      .then(() => {
        logInfo(
          api,
          `sellerclaw-ui: reported ended run of ${childSessionKey} (spawned by ${requesterSessionKey})`,
        );
      })
      .catch((err: unknown) => {
        logError(
          api,
          `sellerclaw-ui: background hand-in report failed run=${runId || "?"} ` +
            `child=${childSessionKey}: ${String(err)}`,
        );
      });
    return undefined;
  });
  logInfo(api, "sellerclaw-ui: background hand-in relay installed");
}
