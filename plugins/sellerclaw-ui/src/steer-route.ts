import type { OpenClawPluginApi } from "openclaw/plugin-sdk/core";

import { readObjectBody, reply, stringField } from "./local-route-body.js";
import { logInfo, logWarn } from "./log.js";

/**
 * Text into a session's active run, at the run's next step (OpenClaw 2026.11
 * `api.session.workflow.steerActiveRun`).
 *
 * The cloud uses it for an update that must not wait for the run to end — today the owner's answer
 * to a question the agent's work is blocked on. A session that is mid-run is never woken with a
 * hook (the gateway's refusal discards the reply being prepared); steering adds the text to the run
 * instead and cancels nothing. The agent reads it as hidden internal context, marked as ours: it
 * carries no owner authority, so approvals are still checked by the backend.
 *
 * Nothing here falls back to anything. Not steered — no active run, a run not taking steering, an
 * OpenClaw without the API — answers `{steered: false, reason}`, and the cloud keeps the update
 * for its next sweep, exactly as when it left a busy session alone.
 */

export const STEER_PATH = "/api/channels/sellerclaw-ui/steer";

export type SteerOutcome = { steered: true; runId?: string } | { steered: false; reason: string };

const MAX_REASON_CHARS = 500;

export async function steerActiveRun(
  api: OpenClawPluginApi,
  guidance: { sessionKey: string; text: string; idempotencyKey?: string },
): Promise<SteerOutcome> {
  const steer = api.session?.workflow?.steerActiveRun;
  if (typeof steer !== "function") {
    return { steered: false, reason: "this OpenClaw cannot steer an active run" };
  }
  try {
    const result = await steer(guidance);
    if (result?.steered === true) {
      return result.runId ? { steered: true, runId: result.runId } : { steered: true };
    }
    return { steered: false, reason: String(result?.reason || "the run did not take the text") };
  } catch (err) {
    const reason = err instanceof Error ? err.message : String(err);
    logWarn(api, `steer: steerActiveRun threw for session=${guidance.sessionKey}: ${reason}`);
    return { steered: false, reason: reason.slice(0, MAX_REASON_CHARS) };
  }
}

/**
 * `POST …/steer` — body `{sessionKey, text, idempotencyKey?}`. Answers 200 with the outcome
 * (`{steered: true, runId?}` / `{steered: false, reason}`); 400 only for a body missing the session
 * or the text.
 */
export function registerSteerRoute(api: OpenClawPluginApi): void {
  api.registerHttpRoute({
    path: STEER_PATH,
    auth: "gateway",
    handler: async (req, res) => {
      const body = await readObjectBody(req, res);
      if (!body) {
        return true;
      }
      const sessionKey = stringField(body, "sessionKey");
      const text = stringField(body, "text");
      if (!sessionKey || !text) {
        return reply(res, 400, { error: "sessionKey and text are required" });
      }
      const idempotencyKey = stringField(body, "idempotencyKey");
      const outcome = await steerActiveRun(api, {
        sessionKey,
        text,
        ...(idempotencyKey ? { idempotencyKey } : {}),
      });
      logInfo(
        api,
        outcome.steered
          ? `steer: steered session=${sessionKey} run=${outcome.runId ?? "?"} key=${idempotencyKey || "-"}`
          : `steer: not steered session=${sessionKey} key=${idempotencyKey || "-"} reason=${outcome.reason}`,
      );
      return reply(res, 200, outcome);
    },
  });
}
