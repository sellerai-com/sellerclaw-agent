import type { IncomingMessage, ServerResponse } from "node:http";

import { beforeEach, describe, expect, it, vi } from "vitest";

const { readBodyMock } = vi.hoisted(() => ({ readBodyMock: vi.fn() }));

vi.mock("openclaw/plugin-sdk/webhook-ingress", () => ({
  readJsonWebhookBodyOrReject: readBodyMock,
}));

import {
  NOT_SINCE_RESTART,
  NO_ANSWER,
  NO_SESSION,
  SESSION_QUESTION_PATH,
  TIMED_OUT,
  UNSUPPORTED,
  registerSessionQuestionRoute,
} from "../session-question-route.js";

type HandlerFn = (req: IncomingMessage, res: ServerResponse) => Promise<boolean>;
type Registered = { path: string; auth: string; handler: HandlerFn };

const logger = { info: vi.fn(), warn: vi.fn(), error: vi.fn() };

function register(forkSession?: unknown): Registered {
  const registerHttpRoute = vi.fn();
  registerSessionQuestionRoute({
    config: {},
    logger,
    registerHttpRoute,
    ...(forkSession === undefined ? {} : { runtime: { subagent: { forkSession } } }),
  } as never);
  return registerHttpRoute.mock.calls[0]![0] as Registered;
}

function res(): ServerResponse & { end: ReturnType<typeof vi.fn> } {
  return { statusCode: 0, end: vi.fn() } as unknown as ServerResponse & { end: ReturnType<typeof vi.fn> };
}

const REQ = { headers: {} } as IncomingMessage;

function sent(r: ServerResponse & { end: ReturnType<typeof vi.fn> }): unknown {
  return JSON.parse(String(r.end.mock.calls[0]?.[0]));
}

function named(name: string, message: string): Error {
  const err = new Error(message);
  err.name = name;
  return err;
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("session question route", () => {
  it("is a gateway-authenticated route", () => {
    const route = register(vi.fn());
    expect(route.path).toBe(SESSION_QUESTION_PATH);
    expect(route.auth).toBe("gateway");
  });

  it("forks the session, asks the question and returns the answer with its usage", async () => {
    const usage = { inputTokens: 1200, outputTokens: 80, totalTokens: 1280, costUsd: 0.0123 };
    const fork = vi.fn().mockResolvedValue({ text: "  Writing the About page.  ", usage });
    readBodyMock.mockResolvedValue({
      ok: true,
      value: { sessionKey: " agent:supervisor:chat-1 ", question: " Is anything stuck? ", timeoutMs: 45_000 },
    });
    const r = res();
    await register(fork).handler(REQ, r);
    expect(fork).toHaveBeenCalledTimes(1);
    const call = fork.mock.calls[0]![0] as Record<string, unknown>;
    expect(call.sessionKey).toBe("agent:supervisor:chat-1");
    expect(call.message).toBe("Is anything stuck?");
    expect(call.timeoutMs).toBe(45_000);
    expect(call.signal).toBeInstanceOf(AbortSignal);
    expect(r.statusCode).toBe(200);
    expect(sent(r)).toEqual({ ok: true, text: "Writing the About page.", usage });
    expect(logger.info).toHaveBeenCalledWith(expect.stringContaining("tokens=1280 cost=0.0123"));
  });

  it.each([
    ["missing", undefined, 45_000],
    ["too short", 10, 5_000],
    ["too long", 10_000_000, 120_000],
    ["not a number", "60s", 45_000],
  ])("keeps a %s timeout within bounds", async (_label, timeoutMs, expected) => {
    const fork = vi.fn().mockResolvedValue({ text: "ok" });
    readBodyMock.mockResolvedValue({ ok: true, value: { sessionKey: "s", question: "q", timeoutMs } });
    await register(fork).handler(REQ, res());
    expect((fork.mock.calls[0]![0] as { timeoutMs: number }).timeoutMs).toBe(expected);
  });

  it("answers without usage when the host measured none", async () => {
    readBodyMock.mockResolvedValue({ ok: true, value: { sessionKey: "s", question: "q" } });
    const r = res();
    await register(vi.fn().mockResolvedValue({ text: "Fine." })).handler(REQ, r);
    expect(sent(r)).toEqual({ ok: true, text: "Fine." });
  });

  it.each([
    [
      "a session that has not run since the restart",
      vi
        .fn()
        .mockRejectedValue(
          new Error(
            "Cache-parity session fork requires a parent turn prepared by this OpenClaw runtime. Send a normal message first.",
          ),
        ),
      NOT_SINCE_RESTART,
    ],
    [
      "a session with no context",
      vi.fn().mockRejectedValue(new Error('No active session context for "agent:supervisor:x".')),
      NO_SESSION,
    ],
    ["a timeout", vi.fn().mockRejectedValue(named("TimeoutError", "The operation was aborted due to timeout")), TIMED_OUT],
    ["an abort", vi.fn().mockRejectedValue(named("AbortError", "This operation was aborted")), TIMED_OUT],
    ["an empty answer from the host", vi.fn().mockRejectedValue(new Error("Session fork produced no answer.")), NO_ANSWER],
    ["a blank answer", vi.fn().mockResolvedValue({ text: "   " }), NO_ANSWER],
    [
      "anything else",
      vi.fn().mockRejectedValue(new Error("Plugin session fork requires plugins.entries.sellerclaw-ui.hooks.allowConversationAccess.")),
      "Plugin session fork requires plugins.entries.sellerclaw-ui.hooks.allowConversationAccess.",
    ],
  ])("explains %s", async (_label, fork, error) => {
    readBodyMock.mockResolvedValue({ ok: true, value: { sessionKey: "s", question: "q" } });
    const r = res();
    await register(fork).handler(REQ, r);
    expect(r.statusCode).toBe(200);
    expect(sent(r)).toEqual({ ok: false, error });
    expect(logger.warn).toHaveBeenCalledWith(expect.stringContaining(error));
  });

  it("says so when this OpenClaw cannot fork a session", async () => {
    readBodyMock.mockResolvedValue({ ok: true, value: { sessionKey: "s", question: "q" } });
    const r = res();
    await register().handler(REQ, r);
    expect(sent(r)).toEqual({ ok: false, error: UNSUPPORTED });
  });

  it.each([
    ["no session", { question: "q" }],
    ["no question", { sessionKey: "s" }],
    ["a blank question", { sessionKey: "s", question: "   " }],
  ])("refuses a body with %s", async (_label, value) => {
    const fork = vi.fn();
    readBodyMock.mockResolvedValue({ ok: true, value });
    const r = res();
    await register(fork).handler(REQ, r);
    expect(r.statusCode).toBe(400);
    expect(sent(r)).toEqual({ error: "sessionKey and question are required" });
    expect(fork).not.toHaveBeenCalled();
  });
});
