import type { IncomingMessage, ServerResponse } from "node:http";

import { beforeEach, describe, expect, it, vi } from "vitest";

const { readBodyMock } = vi.hoisted(() => ({ readBodyMock: vi.fn() }));

vi.mock("openclaw/plugin-sdk/webhook-ingress", () => ({
  readJsonWebhookBodyOrReject: readBodyMock,
}));

import { STEER_PATH, registerSteerRoute } from "../steer-route.js";

type HandlerFn = (req: IncomingMessage, res: ServerResponse) => Promise<boolean>;
type Registered = { path: string; auth: string; handler: HandlerFn };

function register(steerActiveRun?: unknown): Registered {
  const registerHttpRoute = vi.fn();
  registerSteerRoute({
    config: {},
    logger: { info: vi.fn(), warn: vi.fn(), error: vi.fn() },
    registerHttpRoute,
    ...(steerActiveRun === undefined ? {} : { session: { workflow: { steerActiveRun } } }),
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

beforeEach(() => {
  vi.clearAllMocks();
});

describe("steer route", () => {
  it("is a gateway-authenticated route", () => {
    const route = register(vi.fn());
    expect(route.path).toBe(STEER_PATH);
    expect(route.auth).toBe("gateway");
  });

  it("hands the text to the session's active run and reports the run", async () => {
    const steer = vi.fn().mockResolvedValue({ steered: true, runId: "run-1" });
    readBodyMock.mockResolvedValue({
      ok: true,
      value: { sessionKey: " agent:supervisor:chat-1 ", text: " Owner answered: yes ", idempotencyKey: "hook-1" },
    });
    const r = res();
    await register(steer).handler(REQ, r);
    expect(steer).toHaveBeenCalledWith({
      sessionKey: "agent:supervisor:chat-1",
      text: "Owner answered: yes",
      idempotencyKey: "hook-1",
    });
    expect(r.statusCode).toBe(200);
    expect(sent(r)).toEqual({ steered: true, runId: "run-1" });
  });

  it.each([
    [
      "no active run",
      vi.fn().mockResolvedValue({ steered: false, reason: "no active run" }),
      { steered: false, reason: "no active run" },
    ],
    [
      "a refusal without a reason",
      vi.fn().mockResolvedValue({ steered: false }),
      { steered: false, reason: "the run did not take the text" },
    ],
    [
      "a throwing host",
      vi.fn().mockRejectedValue(new Error("plugin instance expired")),
      { steered: false, reason: "plugin instance expired" },
    ],
  ])("answers not steered on %s — and starts nothing else", async (_name, steer, expected) => {
    readBodyMock.mockResolvedValue({ ok: true, value: { sessionKey: "s", text: "t" } });
    const r = res();
    await register(steer).handler(REQ, r);
    expect(r.statusCode).toBe(200);
    expect(sent(r)).toEqual(expected);
  });

  it("answers not steered when the host has no steering API", async () => {
    readBodyMock.mockResolvedValue({ ok: true, value: { sessionKey: "s", text: "t" } });
    const r = res();
    await register(undefined).handler(REQ, r);
    expect(sent(r)).toEqual({ steered: false, reason: "this OpenClaw cannot steer an active run" });
  });

  it("omits an empty idempotency key", async () => {
    const steer = vi.fn().mockResolvedValue({ steered: true });
    readBodyMock.mockResolvedValue({ ok: true, value: { sessionKey: "s", text: "t", idempotencyKey: "" } });
    const r = res();
    await register(steer).handler(REQ, r);
    expect(steer).toHaveBeenCalledWith({ sessionKey: "s", text: "t" });
    expect(sent(r)).toEqual({ steered: true });
  });

  it.each([
    ["no session", { text: "t" }],
    ["no text", { sessionKey: "s" }],
    ["blank text", { sessionKey: "s", text: "   " }],
  ])("refuses a body with %s", async (_name, value) => {
    const steer = vi.fn();
    readBodyMock.mockResolvedValue({ ok: true, value });
    const r = res();
    await register(steer).handler(REQ, r);
    expect(r.statusCode).toBe(400);
    expect(sent(r)).toEqual({ error: "sessionKey and text are required" });
    expect(steer).not.toHaveBeenCalled();
  });
});
