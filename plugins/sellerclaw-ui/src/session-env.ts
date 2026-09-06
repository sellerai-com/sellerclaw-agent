import type { OpenClawPluginApi } from "openclaw/plugin-sdk/core";

/**
 * Tell every shell command which session it runs in.
 *
 * The SellerClaw CLI is how the agent reaches the cloud, and it runs inside OpenClaw's ``exec``
 * tool. That shell knows *which agent* is calling (the workspace path names it) but nothing
 * about the run: OpenClaw puts no session identifier into the environment of the commands it
 * spawns, and the CLI has no other way to learn one. The cloud was left to infer the session
 * from the machine's list of live runs — an inference that has no answer at all once two runs
 * of the same agent are going, which is exactly when it matters: a job created in one chat
 * while another chat is open had its reports addressed to whichever thread came first.
 *
 * ``before_tool_call`` sees the session key of the run making the call and may adjust the
 * call's parameters; ``exec`` takes ``env`` overrides merged over the inherited environment. So
 * every shell the agent opens carries ``SELLERCLAW_SESSION_KEY``, the CLI forwards it as a
 * header, and the cloud attaches a job to the conversation it was asked for in — and an
 * executor's task to the one run doing it — without guessing.
 *
 * Applies to every session, not only our chats: an executor's subagent session is the case the
 * cloud most needs named. Nothing here is gated on conversation access — the hook carries no
 * conversation, only a session key and the call's own parameters.
 */

export const SESSION_KEY_ENV = "SELLERCLAW_SESSION_KEY";

/** Tools that open a shell whose commands may be the SellerClaw CLI. */
const SHELL_TOOL_NAMES = new Set(["exec"]);

interface BeforeToolCallEvent {
  toolName?: unknown;
  params?: unknown;
}

interface HookContext {
  sessionKey?: unknown;
}

interface AdjustedParamsResult {
  params: Record<string, unknown>;
}

function asString(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

/**
 * The adjusted ``exec`` parameters for one call, or ``undefined`` when there is nothing to add:
 * not a shell tool, no session to name, or the shell already names this very session.
 */
export function sessionEnvForToolCall(
  event: BeforeToolCallEvent,
  ctx?: HookContext,
): AdjustedParamsResult | undefined {
  const toolName = asString(event?.toolName).trim();
  if (!SHELL_TOOL_NAMES.has(toolName)) return undefined;
  const sessionKey = asString(ctx?.sessionKey).trim();
  if (!sessionKey) return undefined;
  const params = asRecord(event?.params) ?? {};
  const env = asRecord(params.env) ?? {};
  if (env[SESSION_KEY_ENV] === sessionKey) return undefined;
  return { params: { ...params, env: { ...env, [SESSION_KEY_ENV]: sessionKey } } };
}

export function registerSessionEnv(api: OpenClawPluginApi): void {
  if (typeof api.on !== "function") return;
  api.on("before_tool_call", (event: BeforeToolCallEvent, ctx?: HookContext) =>
    sessionEnvForToolCall(event, ctx),
  );
}
