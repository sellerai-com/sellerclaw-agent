import type { IncomingMessage, ServerResponse } from "node:http";

import type { OpenClawPluginApi } from "openclaw/plugin-sdk/core";
import { readJsonWebhookBodyOrReject } from "openclaw/plugin-sdk/webhook-ingress";

import { enterCronStandby, executeCronCommand, leaveCronStandby } from "./cron-sync.js";
import { logWarn } from "./log.js";

/**
 * Local routes the agent server drives the cron sync through (gateway-token authenticated, like
 * the chat inbound route): one `cron_command` from the cloud, and standby on/off when another
 * agent instance takes the owner's session over or gives it back.
 */

export const CRON_COMMAND_PATH = "/api/channels/sellerclaw-ui/cron/command";
export const CRON_STANDBY_PATH = "/api/channels/sellerclaw-ui/cron/standby";

const COMMAND_ACTIONS = new Set(["add", "update", "remove", "run"]);

function reply(res: ServerResponse, status: number, body: unknown): true {
  res.statusCode = status;
  res.end(JSON.stringify(body));
  return true;
}

async function readObjectBody(
  req: IncomingMessage,
  res: ServerResponse,
): Promise<Record<string, unknown> | null> {
  const readResult = await readJsonWebhookBodyOrReject({ req, res });
  if (
    !readResult ||
    typeof readResult !== "object" ||
    !("ok" in readResult) ||
    !(readResult as { ok: boolean }).ok
  ) {
    return null; // already answered by the reader
  }
  const body = (readResult as { ok: true; value: unknown }).value;
  if (!body || typeof body !== "object" || Array.isArray(body)) {
    reply(res, 400, { error: "Invalid JSON body" });
    return null;
  }
  return body as Record<string, unknown>;
}

/**
 * `POST …/cron/command` — body: the `cron_command` frame. Answers 200 with the outcome the agent
 * server reports to the cloud (`{ok, error, store_epoch, job, run_id}`), a failed command included;
 * 400 only for a frame that is not a command at all.
 */
export function registerCronCommandRoute(api: OpenClawPluginApi): void {
  api.registerHttpRoute({
    path: CRON_COMMAND_PATH,
    auth: "gateway",
    handler: async (req, res) => {
      const body = await readObjectBody(req, res);
      if (!body) return true;
      const action = typeof body.action === "string" ? body.action : "";
      if (!COMMAND_ACTIONS.has(action)) {
        return reply(res, 400, { error: "action must be add, update, remove or run" });
      }
      const result = await executeCronCommand(body);
      if (!result.ok) {
        logWarn(
          api,
          `sellerclaw-ui[cron]: command ${action} request_id=${String(body.request_id ?? "")} failed: ${result.error ?? ""}`,
        );
      }
      return reply(res, 200, result);
    },
  });
}

/**
 * `POST …/cron/standby` — body `{standby: boolean}`. 200 once the state holds (jobs disabled, or
 * re-enabled and reporting resumed), 503 while it does not yet — the agent server retries.
 */
export function registerCronStandbyRoute(api: OpenClawPluginApi): void {
  api.registerHttpRoute({
    path: CRON_STANDBY_PATH,
    auth: "gateway",
    handler: async (req, res) => {
      const body = await readObjectBody(req, res);
      if (!body) return true;
      if (typeof body.standby !== "boolean") {
        return reply(res, 400, { error: "standby must be a boolean" });
      }
      try {
        const outcome = body.standby ? await enterCronStandby() : await leaveCronStandby();
        return reply(res, outcome.ok ? 200 : 503, {
          ok: outcome.ok,
          standby: outcome.standby,
          job_ids: outcome.jobIds,
          error: outcome.error,
        });
      } catch (err) {
        return reply(res, 503, { ok: false, error: err instanceof Error ? err.message : String(err) });
      }
    },
  });
}
