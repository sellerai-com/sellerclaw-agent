import type { OpenClawPluginApi } from "openclaw/plugin-sdk/core";

import { readObjectBody, reply, stringField } from "./local-route-body.js";
import { logInfo, logWarn } from "./log.js";

/**
 * A read-only side question to one of the owner's sessions (OpenClaw 2026.11
 * `api.runtime.subagent.forkSession`), for SellerClaw staff support.
 *
 * The fork replays the session as it stands — its instructions, the conversation, what the agent
 * did — and answers one more message with tools denied. It writes no transcript, delivers nothing
 * to any channel and touches neither queued messages nor steering, so the owner and the chat never
 * see it. OpenClaw can fork only a session that ran a turn since the gateway last started; anything
 * else, like a timeout, comes back as `{ok: false, error}` in words staff can act on.
 */

export const SESSION_QUESTION_PATH = "/api/channels/sellerclaw-ui/session/question";

export type ForkUsage = {
  inputTokens?: number;
  outputTokens?: number;
  cacheReadTokens?: number;
  cacheWriteTokens?: number;
  totalTokens?: number;
  costUsd?: number;
};

export type SessionQuestionOutcome =
  | { ok: true; text: string; usage?: ForkUsage }
  | { ok: false; error: string };

const DEFAULT_TIMEOUT_MS = 45_000;
const MIN_TIMEOUT_MS = 5_000;
const MAX_TIMEOUT_MS = 120_000;
const MAX_ERROR_CHARS = 500;

export const NOT_SINCE_RESTART =
  "This chat's session hasn't run since the agent last restarted. Ask again after the user's next message.";
export const NO_SESSION = "This chat has no live session on the agent (it never ran there, or it was reset).";
export const TIMED_OUT = "The agent didn't answer in time. Try again in a minute.";
export const NO_ANSWER = "The agent gave an empty answer. Try again.";
export const UNSUPPORTED = "This agent's OpenClaw cannot answer questions about a session.";

function timeoutFrom(value: unknown): number {
  if (typeof value !== "number" || !Number.isFinite(value)) return DEFAULT_TIMEOUT_MS;
  return Math.min(MAX_TIMEOUT_MS, Math.max(MIN_TIMEOUT_MS, Math.floor(value)));
}

/** What went wrong, in words staff can act on; OpenClaw's own message when nothing fits. */
export function explainForkError(err: unknown): string {
  const name = err instanceof Error ? err.name : "";
  const message = err instanceof Error ? err.message : String(err);
  if (name === "TimeoutError" || name === "AbortError") return TIMED_OUT;
  if (/parent turn prepared|Send a normal message first/i.test(message)) return NOT_SINCE_RESTART;
  if (/No active session context/i.test(message)) return NO_SESSION;
  if (/produced no answer/i.test(message)) return NO_ANSWER;
  return message.slice(0, MAX_ERROR_CHARS) || "The agent could not answer.";
}

export async function askSession(
  api: OpenClawPluginApi,
  params: { sessionKey: string; question: string; timeoutMs: number },
): Promise<SessionQuestionOutcome> {
  const fork = api.runtime?.subagent?.forkSession;
  if (typeof fork !== "function") {
    return { ok: false, error: UNSUPPORTED };
  }
  try {
    // One budget for everything: waiting for a free subagent slot counts too.
    const result = await fork({
      sessionKey: params.sessionKey,
      message: params.question,
      timeoutMs: params.timeoutMs,
      signal: AbortSignal.timeout(params.timeoutMs),
    });
    const text = typeof result?.text === "string" ? result.text.trim() : "";
    if (!text) return { ok: false, error: NO_ANSWER };
    return result.usage ? { ok: true, text, usage: result.usage } : { ok: true, text };
  } catch (err) {
    return { ok: false, error: explainForkError(err) };
  }
}

/**
 * `POST …/session/question` — body `{sessionKey, question, timeoutMs?}`. Answers 200 with the
 * outcome (`{ok: true, text, usage?}` / `{ok: false, error}`); 400 only for a body missing the
 * session or the question.
 */
export function registerSessionQuestionRoute(api: OpenClawPluginApi): void {
  api.registerHttpRoute({
    path: SESSION_QUESTION_PATH,
    auth: "gateway",
    handler: async (req, res) => {
      const body = await readObjectBody(req, res);
      if (!body) {
        return true;
      }
      const sessionKey = stringField(body, "sessionKey");
      const question = stringField(body, "question");
      if (!sessionKey || !question) {
        return reply(res, 400, { error: "sessionKey and question are required" });
      }
      const started = Date.now();
      const outcome = await askSession(api, { sessionKey, question, timeoutMs: timeoutFrom(body.timeoutMs) });
      const took = `${Date.now() - started}ms`;
      if (outcome.ok) {
        logInfo(
          api,
          `session-question: answered session=${sessionKey} in ${took} tokens=${outcome.usage?.totalTokens ?? "?"} cost=${outcome.usage?.costUsd ?? "?"}`,
        );
      } else {
        logWarn(api, `session-question: not answered session=${sessionKey} in ${took}: ${outcome.error}`);
      }
      return reply(res, 200, outcome);
    },
  });
}
