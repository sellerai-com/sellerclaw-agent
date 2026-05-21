import { beforeEach, describe, expect, it, vi } from "vitest";
import type { IncomingMessage, ServerResponse } from "node:http";

const { resolveSessionMock, abortRunMock, readBodyMock } = vi.hoisted(() => ({
  resolveSessionMock: vi.fn(),
  abortRunMock: vi.fn(),
  readBodyMock: vi.fn(),
}));

vi.mock("openclaw/plugin-sdk/agent-harness-runtime", () => ({
  abortAgentHarnessRun: (...args: unknown[]) => abortRunMock(...args),
  resolveActiveEmbeddedRunSessionId: (...args: unknown[]) => resolveSessionMock(...args),
}));

vi.mock("openclaw/plugin-sdk/webhook-ingress", () => ({
  readJsonWebhookBodyOrReject: readBodyMock,
}));

import { registerAbortRoute } from "../inbound.js";
import type { OpenClawPluginApi } from "openclaw/plugin-sdk/core";

const DEFAULT_CONFIG = {
  channels: {
    "sellerclaw-ui": {
      apiBaseUrl: "https://api.example",
      userId: "user-1",
      agentApiKey: "sca",
      internalWebhookSecret: "secret",
    },
  },
} as Record<string, unknown>;

function buildApi() {
  const registerHttpRoute = vi.fn();
  const api = {
    config: DEFAULT_CONFIG,
    logger: { info: vi.fn(), warn: vi.fn(), error: vi.fn() },
    registerHttpRoute,
  };
  return { api: api as unknown as OpenClawPluginApi, registerHttpRoute };
}

type HandlerFn = (req: IncomingMessage, res: ServerResponse) => Promise<boolean>;

function getHandler(registerHttpRoute: ReturnType<typeof vi.fn>): HandlerFn {
  return registerHttpRoute.mock.calls[0]![0].handler as HandlerFn;
}

function makeReq(
  headers: Record<string, string> = { authorization: "Bearer secret" },
): IncomingMessage {
  return { headers } as unknown as IncomingMessage;
}

function makeRes(): ServerResponse & { body: string } {
  const res = {
    statusCode: 0,
    end: vi.fn((chunk?: string) => {
      (res as unknown as { body: string }).body = chunk ?? "";
    }),
  } as unknown as ServerResponse & { body: string };
  return res;
}

describe("registerAbortRoute", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("registers HTTP route /channels/sellerclaw-ui/abort with plugin auth", () => {
    const { api, registerHttpRoute } = buildApi();
    registerAbortRoute(api);
    expect(registerHttpRoute).toHaveBeenCalledTimes(1);
    const opts = registerHttpRoute.mock.calls[0]![0] as {
      path: string;
      auth: string;
      handler: unknown;
    };
    expect(opts.path).toBe("/channels/sellerclaw-ui/abort");
    expect(opts.auth).toBe("plugin");
    expect(typeof opts.handler).toBe("function");
  });

  it("aborts the active run for the chat's session key", async () => {
    resolveSessionMock.mockReturnValue("sess-1");
    readBodyMock.mockResolvedValue({ ok: true, value: { chat_id: "c1", agent_id: "supervisor" } });
    const { api, registerHttpRoute } = buildApi();
    registerAbortRoute(api);
    const handler = getHandler(registerHttpRoute);

    const res = makeRes();
    const done = await handler(makeReq(), res);

    expect(done).toBe(true);
    expect(resolveSessionMock).toHaveBeenCalledWith("agent:supervisor:sellerclaw-ui:direct:c1");
    expect(abortRunMock).toHaveBeenCalledWith("sess-1");
    expect(res.statusCode).toBe(202);
    expect(JSON.parse(res.body)).toEqual({ ok: true, aborted: true });
  });

  it("is a benign no-op when no active run matches the session", async () => {
    resolveSessionMock.mockReturnValue(null);
    readBodyMock.mockResolvedValue({ ok: true, value: { chat_id: "c1", agent_id: "supervisor" } });
    const { api, registerHttpRoute } = buildApi();
    registerAbortRoute(api);
    const handler = getHandler(registerHttpRoute);

    const res = makeRes();
    await handler(makeReq(), res);

    expect(abortRunMock).not.toHaveBeenCalled();
    expect(res.statusCode).toBe(202);
    expect(JSON.parse(res.body)).toEqual({ ok: true, aborted: false });
  });

  it("returns 401 when the bearer token does not match", async () => {
    const { api, registerHttpRoute } = buildApi();
    registerAbortRoute(api);
    const handler = getHandler(registerHttpRoute);

    const res = makeRes();
    await handler(makeReq({ authorization: "Bearer wrong" }), res);

    expect(res.statusCode).toBe(401);
    expect(readBodyMock).not.toHaveBeenCalled();
    expect(abortRunMock).not.toHaveBeenCalled();
  });

  it("returns 400 when chat_id or agent_id is missing", async () => {
    readBodyMock.mockResolvedValue({ ok: true, value: { chat_id: "c1" } });
    const { api, registerHttpRoute } = buildApi();
    registerAbortRoute(api);
    const handler = getHandler(registerHttpRoute);

    const res = makeRes();
    await handler(makeReq(), res);

    expect(res.statusCode).toBe(400);
    expect(abortRunMock).not.toHaveBeenCalled();
  });
});
