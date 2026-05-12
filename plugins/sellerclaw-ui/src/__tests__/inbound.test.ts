import { beforeEach, describe, expect, it, vi } from "vitest";
import type { IncomingMessage, ServerResponse } from "node:http";

const { dispatchMock, readBodyMock, postWebhookMock, saveMediaBufferMock } = vi.hoisted(() => ({
  dispatchMock: vi.fn().mockResolvedValue(undefined),
  readBodyMock: vi.fn(),
  postWebhookMock: vi.fn().mockResolvedValue(new Response(null, { status: 200 })),
  saveMediaBufferMock: vi.fn(),
}));

vi.mock("openclaw/plugin-sdk/channel-inbound", () => ({
  dispatchInboundDirectDmWithRuntime: (...args: unknown[]) => dispatchMock(...args),
}));

vi.mock("openclaw/plugin-sdk/media-store", () => ({
  saveMediaBuffer: (...args: unknown[]) => saveMediaBufferMock(...args),
  resolveMediaBufferPath: vi.fn(),
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
  return { api, registerHttpRoute };
}

type HandlerFn = (req: IncomingMessage, res: ServerResponse) => Promise<boolean>;

function getHandler(registerHttpRoute: ReturnType<typeof vi.fn>): HandlerFn {
  return registerHttpRoute.mock.calls[0]![0].handler as HandlerFn;
}

function setFetchResponses(
  responses: Array<{ ok: boolean; status?: number; body?: ArrayBuffer; contentType?: string }>,
): { fetchMock: ReturnType<typeof vi.fn> } {
  const fetchMock = vi.fn();
  for (const r of responses) {
    fetchMock.mockResolvedValueOnce({
      ok: r.ok,
      status: r.status ?? (r.ok ? 200 : 500),
      headers: { get: () => r.contentType ?? "application/octet-stream" },
      arrayBuffer: async () => r.body ?? new ArrayBuffer(0),
    });
  }
  globalThis.fetch = fetchMock as unknown as typeof fetch;
  return { fetchMock };
}

describe("registerInboundRoute", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    dispatchMock.mockResolvedValue(undefined);
    postWebhookMock.mockResolvedValue(new Response(null, { status: 200 }));
    saveMediaBufferMock.mockReset();
    saveMediaBufferMock.mockResolvedValue({
      id: "saved-id",
      path: "/home/node/.openclaw/media/inbound/saved-id.jpg",
      size: 0,
      contentType: "image/jpeg",
    });
  });

  it("registers HTTP route /channels/sellerclaw-ui/inbound with plugin auth", () => {
    const registerHttpRoute = vi.fn();
    const api = {
      config: {
        channels: {
          "sellerclaw-ui": {
            apiBaseUrl: "https://api.example",
            userId: "user-1",
            agentApiKey: "sca",
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
            agentApiKey: "sca",
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
            agentApiKey: "sca",
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
            agentApiKey: "sca",
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
        Authorization: "Bearer sca",
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

  it("persists image attachments via saveMediaBuffer and injects [Image: source: ...] marker", async () => {
    readBodyMock.mockResolvedValue({
      ok: true,
      value: {
        chat_id: "c1",
        agent_id: "supervisor",
        user_id: "u1",
        text: "Describe",
        raw_content: [
          { type: "text", text: "Describe" },
          {
            type: "image_url",
            image_url: { url: "http://localhost:8000/agent/files/abc/photo.jpg" },
            filename: "photo.jpg",
            content_type: "image/jpeg",
          },
        ],
      },
    });
    const { fetchMock } = setFetchResponses([
      { ok: true, body: new ArrayBuffer(8), contentType: "image/jpeg" },
    ]);

    const { api, registerHttpRoute } = buildApi();
    registerInboundRoute(api as import("openclaw/plugin-sdk/core").OpenClawPluginApi);
    const handler = getHandler(registerHttpRoute);

    const req = { headers: { authorization: "Bearer secret" } } as IncomingMessage;
    const res = { statusCode: 0, end: vi.fn() } as unknown as ServerResponse;

    await handler(req, res);
    expect(res.statusCode).toBe(202);

    await vi.waitFor(() => {
      expect(dispatchMock).toHaveBeenCalledTimes(1);
    });

    // URL host rewritten from localhost:8000 to apiBaseUrl host (api.example).
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [fetchedUrl, fetchInit] = fetchMock.mock.calls[0] as [
      string,
      { headers: Record<string, string> },
    ];
    expect(fetchedUrl).toBe("https://api.example/agent/files/abc/photo.jpg");
    expect(fetchInit.headers.Authorization).toBe("Bearer sca");

    expect(saveMediaBufferMock).toHaveBeenCalledTimes(1);
    const saveArgs = saveMediaBufferMock.mock.calls[0]!;
    expect(Buffer.isBuffer(saveArgs[0])).toBe(true);
    expect(saveArgs[1]).toBe("image/jpeg");
    expect(saveArgs[2]).toBe("inbound");
    expect(saveArgs[4]).toBe("photo.jpg");

    const d = dispatchMock.mock.calls[0]![0] as Record<string, unknown>;
    expect(d.rawBody).toBe(
      "Describe\n[Image: source: /home/node/.openclaw/media/inbound/saved-id.jpg]",
    );
    expect(d.mediaUrls).toBeUndefined();
    expect(d.mediaPaths).toBeUndefined();
    expect(d.rawContent).toBeUndefined();
  });

  it("falls back to [attachment unavailable] when image fetch fails", async () => {
    readBodyMock.mockResolvedValue({
      ok: true,
      value: {
        chat_id: "c1",
        agent_id: "supervisor",
        user_id: "u1",
        text: "Describe",
        raw_content: [
          { type: "text", text: "Describe" },
          {
            type: "image_url",
            image_url: { url: "https://api.example/agent/files/x/photo.png" },
            filename: "photo.png",
            content_type: "image/png",
          },
        ],
      },
    });
    setFetchResponses([{ ok: false, status: 404 }]);

    const { api, registerHttpRoute } = buildApi();
    registerInboundRoute(api as import("openclaw/plugin-sdk/core").OpenClawPluginApi);
    const handler = getHandler(registerHttpRoute);

    const req = { headers: { authorization: "Bearer secret" } } as IncomingMessage;
    const res = { statusCode: 0, end: vi.fn() } as unknown as ServerResponse;

    await handler(req, res);
    expect(res.statusCode).toBe(202);

    await vi.waitFor(() => {
      expect(dispatchMock).toHaveBeenCalledTimes(1);
    });

    expect(saveMediaBufferMock).not.toHaveBeenCalled();
    const d = dispatchMock.mock.calls[0]![0] as { rawBody: string };
    expect(d.rawBody).toBe("Describe\n[attachment unavailable: photo.png]");
  });

  it("renders non-image file_url parts as markdown links with rewritten host", async () => {
    readBodyMock.mockResolvedValue({
      ok: true,
      value: {
        chat_id: "c1",
        agent_id: "supervisor",
        user_id: "u1",
        text: "Look",
        raw_content: [
          { type: "text", text: "Look" },
          {
            type: "file_url",
            file_url: { url: "http://localhost:8000/agent/files/x/report.csv" },
            filename: "report.csv",
            content_type: "text/csv",
          },
        ],
      },
    });
    const { fetchMock } = setFetchResponses([]);

    const { api, registerHttpRoute } = buildApi();
    registerInboundRoute(api as import("openclaw/plugin-sdk/core").OpenClawPluginApi);
    const handler = getHandler(registerHttpRoute);

    const req = { headers: { authorization: "Bearer secret" } } as IncomingMessage;
    const res = { statusCode: 0, end: vi.fn() } as unknown as ServerResponse;

    await handler(req, res);
    expect(res.statusCode).toBe(202);

    await vi.waitFor(() => {
      expect(dispatchMock).toHaveBeenCalledTimes(1);
    });

    expect(fetchMock).not.toHaveBeenCalled();
    expect(saveMediaBufferMock).not.toHaveBeenCalled();
    const d = dispatchMock.mock.calls[0]![0] as { rawBody: string };
    expect(d.rawBody).toBe(
      "Look\n[report.csv](https://api.example/agent/files/x/report.csv)",
    );
  });

  it("handles mixed image + file in one inbound message", async () => {
    readBodyMock.mockResolvedValue({
      ok: true,
      value: {
        chat_id: "c1",
        agent_id: "supervisor",
        user_id: "u1",
        text: "see both",
        raw_content: [
          { type: "text", text: "see both" },
          {
            type: "image_url",
            image_url: { url: "https://api.example/agent/files/i/pic.jpg" },
            filename: "pic.jpg",
            content_type: "image/jpeg",
          },
          {
            type: "file_url",
            file_url: { url: "https://api.example/agent/files/f/data.json" },
            filename: "data.json",
            content_type: "application/json",
          },
        ],
      },
    });
    setFetchResponses([{ ok: true, body: new ArrayBuffer(4), contentType: "image/jpeg" }]);

    const { api, registerHttpRoute } = buildApi();
    registerInboundRoute(api as import("openclaw/plugin-sdk/core").OpenClawPluginApi);
    const handler = getHandler(registerHttpRoute);

    const req = { headers: { authorization: "Bearer secret" } } as IncomingMessage;
    const res = { statusCode: 0, end: vi.fn() } as unknown as ServerResponse;

    await handler(req, res);
    expect(res.statusCode).toBe(202);

    await vi.waitFor(() => {
      expect(dispatchMock).toHaveBeenCalledTimes(1);
    });

    const d = dispatchMock.mock.calls[0]![0] as { rawBody: string };
    expect(d.rawBody).toBe(
      "see both\n[Image: source: /home/node/.openclaw/media/inbound/saved-id.jpg]\n[data.json](https://api.example/agent/files/f/data.json)",
    );
  });
});
