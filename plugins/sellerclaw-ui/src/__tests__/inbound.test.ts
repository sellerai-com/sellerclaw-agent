import { beforeEach, describe, expect, it, vi } from "vitest";
import type { IncomingMessage, ServerResponse } from "node:http";

const { dispatchMock, readBodyMock, postWebhookMock } = vi.hoisted(() => ({
  dispatchMock: vi.fn().mockResolvedValue(undefined),
  readBodyMock: vi.fn(),
  postWebhookMock: vi.fn().mockResolvedValue(new Response(null, { status: 200 })),
}));

vi.mock("openclaw/plugin-sdk/channel-inbound", () => ({
  dispatchInboundDirectDmWithRuntime: (...args: unknown[]) => dispatchMock(...args),
}));

vi.mock("../send.js", async () => {
  const actual = await vi.importActual<typeof import("../send.js")>("../send.js");
  return {
    ...actual,
    postOpenclawWebhook: (...args: unknown[]) => postWebhookMock(...args),
  };
});

vi.mock("openclaw/plugin-sdk/webhook-ingress", () => ({
  readJsonWebhookBodyOrReject: readBodyMock,
}));

vi.mock("../runtime-store.js", () => ({
  getRuntime: () => ({}),
}));

import { registerInboundRoute } from "../inbound.js";

describe("registerInboundRoute", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    dispatchMock.mockResolvedValue(undefined);
    postWebhookMock.mockResolvedValue(new Response(null, { status: 200 }));
  });

  it("registers HTTP route /channels/sellerclaw-ui/inbound with plugin auth", () => {
    const registerHttpRoute = vi.fn();
    const api = {
      config: {
        channels: {
          "sellerclaw-ui": {
            apiBaseUrl: "https://api.example",
            userId: "user-1",
            internalWebhookSecret: "secret",
          },
        },
      } as Record<string, unknown>,
      logger: { info: vi.fn(), warn: vi.fn(), error: vi.fn() },
      registerHttpRoute,
    };

    registerInboundRoute(api as import("openclaw/plugin-sdk/core").OpenClawPluginApi);

    expect(registerHttpRoute).toHaveBeenCalledTimes(1);
    const opts = registerHttpRoute.mock.calls[0]![0] as {
      path: string;
      auth: string;
      handler: unknown;
    };
    expect(opts.path).toBe("/channels/sellerclaw-ui/inbound");
    expect(opts.auth).toBe("plugin");
    expect(typeof opts.handler).toBe("function");
  });

  it("returns 401 when Authorization is missing", async () => {
    const registerHttpRoute = vi.fn();
    const api = {
      config: {
        channels: {
          "sellerclaw-ui": {
            apiBaseUrl: "https://api.example",
            userId: "user-1",
            internalWebhookSecret: "secret",
          },
        },
      } as Record<string, unknown>,
      logger: { info: vi.fn(), warn: vi.fn(), error: vi.fn() },
      registerHttpRoute,
    };
    registerInboundRoute(api as import("openclaw/plugin-sdk/core").OpenClawPluginApi);
    const handler = registerHttpRoute.mock.calls[0]![0].handler as (
      req: IncomingMessage,
      res: ServerResponse,
    ) => Promise<boolean>;

    const req = {
      headers: {},
    } as IncomingMessage;
    const res = {
      statusCode: 0,
      end: vi.fn(),
    } as unknown as ServerResponse;

    const done = await handler(req, res);
    expect(done).toBe(true);
    expect(res.statusCode).toBe(401);
    expect(readBodyMock).not.toHaveBeenCalled();
  });

  it("returns 400 when chat_id or text is missing", async () => {
    readBodyMock.mockResolvedValue({
      ok: true,
      value: { chat_id: "", text: "" },
    });

    const registerHttpRoute = vi.fn();
    const api = {
      config: {
        channels: {
          "sellerclaw-ui": {
            apiBaseUrl: "https://api.example",
            userId: "user-1",
            internalWebhookSecret: "secret",
          },
        },
      } as Record<string, unknown>,
      logger: { info: vi.fn(), warn: vi.fn(), error: vi.fn() },
      registerHttpRoute,
    };
    registerInboundRoute(api as import("openclaw/plugin-sdk/core").OpenClawPluginApi);
    const handler = registerHttpRoute.mock.calls[0]![0].handler as (
      req: IncomingMessage,
      res: ServerResponse,
    ) => Promise<boolean>;

    const req = {
      headers: { authorization: "Bearer secret" },
    } as IncomingMessage;
    const res = {
      statusCode: 0,
      end: vi.fn(),
    } as unknown as ServerResponse;

    await handler(req, res);
    expect(res.statusCode).toBe(400);
    expect(dispatchMock).not.toHaveBeenCalled();
  });

  it("dispatches inbound and posts stream-delta and stream-end", async () => {
    readBodyMock.mockResolvedValue({
      ok: true,
      value: {
        chat_id: "c1",
        agent_id: "supervisor",
        user_id: "u1",
        text: " hi ",
      },
    });

    const registerHttpRoute = vi.fn();
    const api = {
      config: {
        channels: {
          "sellerclaw-ui": {
            apiBaseUrl: "https://api.example",
            userId: "user-1",
            internalWebhookSecret: "secret",
          },
        },
      } as Record<string, unknown>,
      logger: { info: vi.fn(), warn: vi.fn(), error: vi.fn() },
      registerHttpRoute,
    };
    registerInboundRoute(api as import("openclaw/plugin-sdk/core").OpenClawPluginApi);
    const handler = registerHttpRoute.mock.calls[0]![0].handler as (
      req: IncomingMessage,
      res: ServerResponse,
    ) => Promise<boolean>;

    const req = {
      headers: { authorization: "Bearer secret" },
    } as IncomingMessage;
    const res = {
      statusCode: 0,
      end: vi.fn(),
    } as unknown as ServerResponse;

    await handler(req, res);
    expect(res.statusCode).toBe(202);

    expect(dispatchMock).toHaveBeenCalledTimes(1);
    const arg = dispatchMock.mock.calls[0]![0] as {
      deliver: (p: { text: string }) => Promise<void>;
    };
    await arg.deliver({ text: "chunk" });

    const streamDeltaCalls = postWebhookMock.mock.calls.filter((c) =>
      String(c[0]).includes("/internal/openclaw/stream-delta"),
    );
    expect(streamDeltaCalls.length).toBeGreaterThanOrEqual(1);
    const [, init] = streamDeltaCalls[0]!;
    expect(init).toMatchObject({
      method: "POST",
      headers: expect.objectContaining({
        Authorization: "Bearer secret",
        "Content-Type": "application/json",
      }),
    });
    const body = JSON.parse(String((init as RequestInit).body)) as Record<string, string>;
    expect(body.text).toBe("chunk");
    expect(body.session_key).toBe("agent:supervisor:sellerclaw-ui:direct:c1");

    await vi.waitFor(() => {
      const endCalls = postWebhookMock.mock.calls.filter((c) =>
        String(c[0]).includes("/internal/openclaw/stream-end"),
      );
      expect(endCalls.length).toBeGreaterThanOrEqual(1);
    });
  });

  it("passes raw_content and mediaUrls to dispatch when attachments present", async () => {
    readBodyMock.mockResolvedValue({
      ok: true,
      value: {
        chat_id: "c1",
        agent_id: "supervisor",
        user_id: "u1",
        text: "Describe",
        raw_content: [
          { type: "text", text: "Describe" },
          { type: "image_url", image_url: { url: "https://cdn.example/x.png" } },
        ],
      },
    });

    const registerHttpRoute = vi.fn();
    const api = {
      config: {
        channels: {
          "sellerclaw-ui": {
            apiBaseUrl: "https://api.example",
            userId: "user-1",
            internalWebhookSecret: "secret",
          },
        },
      } as Record<string, unknown>,
      logger: { info: vi.fn(), warn: vi.fn(), error: vi.fn() },
      registerHttpRoute,
    };
    registerInboundRoute(api as import("openclaw/plugin-sdk/core").OpenClawPluginApi);
    const handler = registerHttpRoute.mock.calls[0]![0].handler as (
      req: IncomingMessage,
      res: ServerResponse,
    ) => Promise<boolean>;

    const req = { headers: { authorization: "Bearer secret" } } as IncomingMessage;
    const res = { statusCode: 0, end: vi.fn() } as unknown as ServerResponse;

    await handler(req, res);
    expect(res.statusCode).toBe(202);
    expect(dispatchMock).toHaveBeenCalledTimes(1);
    const d = dispatchMock.mock.calls[0]![0] as {
      rawContent: unknown[];
      mediaUrls: string[];
      mediaPaths: string[];
      rawBody: string;
    };
    expect(d.rawBody).toBe("Describe");
    expect(d.mediaUrls).toEqual(["https://cdn.example/x.png"]);
    expect(d.mediaPaths).toEqual(["https://cdn.example/x.png"]);
    expect(Array.isArray(d.rawContent)).toBe(true);
    expect((d.rawContent as { type: string }[]).length).toBe(2);
  });
});
