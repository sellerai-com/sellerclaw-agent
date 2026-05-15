import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { extractChatIdFromAddress, sellerclawUiChannelPlugin, setPluginConfig } from "../channel.js";
import type { OpenClawConfig } from "openclaw/plugin-sdk/core";
import {
  isTransientWebhookStatus,
  postOpenclawWebhook,
  postWebhookMediaMessage,
  postWebhookMessage,
  resolveOutboundExtId,
  resolveOutboundMediaUrl,
  type ScwUiAccount,
  WEBHOOK_MAX_ATTEMPTS,
} from "../send.js";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const account: ScwUiAccount = {
  apiBaseUrl: "https://api.example.com/",
  userId: "550e8400-e29b-41d4-a716-446655440000",
  agentApiKey: "sca-agent-key",
  internalWebhookSecret: "hooks-delivery-token",
  localAgentBaseUrl: "http://127.0.0.1:8001",
};

describe("isTransientWebhookStatus", () => {
  it.each([
    [408, true],
    [425, true],
    [429, true],
    [500, true],
    [599, true],
    [400, false],
    [401, false],
    [404, false],
    [499, false],
    [600, false],
  ])("status %i → %s", (status, expected) => {
    expect(isTransientWebhookStatus(status)).toBe(expected);
  });
});

describe("resolveOutboundExtId", () => {
  it("uses messageId when present", () => {
    expect(resolveOutboundExtId({ messageId: "m1" })).toBe("m1");
  });

  it("uses clientMessageId when messageId absent", () => {
    expect(resolveOutboundExtId({ clientMessageId: "c1" })).toBe("c1");
  });

  it("prefers messageId over clientMessageId", () => {
    expect(
      resolveOutboundExtId({ messageId: "m", clientMessageId: "c" }),
    ).toBe("m");
  });

  it("falls back to random UUID", () => {
    const id = resolveOutboundExtId({});
    expect(id).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i,
    );
  });
});

describe("postOpenclawWebhook / postWebhookMessage", () => {
  const originalFetch = globalThis.fetch;

  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("returns messageId from JSON body on success", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({ message: { id: "from-api" } }, 200),
    );
    globalThis.fetch = fetchMock;

    const p = postWebhookMessage(account, "session-1", {
      text: "hi",
      message_id: "ext-1",
    });
    await vi.runAllTimersAsync();
    const result = await p;

    expect(result).toEqual({ messageId: "from-api" });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(init.method).toBe("POST");
    expect(init.headers).toMatchObject({
      "Content-Type": "application/json",
      Authorization: "Bearer sca-agent-key",
    });
    const body = JSON.parse(init.body as string) as Record<string, unknown>;
    expect(body).toMatchObject({
      user_id: account.userId,
      session_key: "session-1",
      text: "hi",
      message_id: "ext-1",
    });
  });

  it("sends Bearer agentApiKey for cloud webhook", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({ message: { id: "x" } }, 200),
    );
    globalThis.fetch = fetchMock;

    const acc: ScwUiAccount = {
      apiBaseUrl: "https://api.example.com",
      userId: "550e8400-e29b-41d4-a716-446655440000",
      agentApiKey: "my-agent-key",
      internalWebhookSecret: "hooks-only-local",
      localAgentBaseUrl: "http://127.0.0.1:8001",
    };
    const promise = postWebhookMessage(acc, "sk", { text: "t", message_id: "m" });
    await vi.runAllTimersAsync();
    await promise;

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(init.headers).toMatchObject({ Authorization: "Bearer my-agent-key" });
  });

  it("strips trailing slash from apiBaseUrl", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ message: { id: "x" } }, 200));
    globalThis.fetch = fetchMock;

    const promise = postWebhookMessage(account, "sk", { text: "t", message_id: "m" });
    await vi.runAllTimersAsync();
    await promise;

    const [url] = fetchMock.mock.calls[0] as [string];
    expect(url).toBe("https://api.example.com/internal/openclaw/messages");
  });

  it("retries on HTTP 500 up to WEBHOOK_MAX_ATTEMPTS then succeeds", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(new Response("err", { status: 500 }))
      .mockResolvedValueOnce(new Response("err", { status: 500 }))
      .mockResolvedValueOnce(new Response("err", { status: 500 }))
      .mockResolvedValueOnce(jsonResponse({ message: { id: "ok" } }, 200));
    globalThis.fetch = fetchMock;

    const promise = postWebhookMessage(account, "sk", { text: "t", message_id: "m" });
    await vi.runAllTimersAsync();
    const result = await promise;

    expect(result.messageId).toBe("ok");
    expect(fetchMock).toHaveBeenCalledTimes(4);
  });

  it("does not retry on HTTP 401", async () => {
    vi.useRealTimers();
    const fetchMock = vi.fn().mockResolvedValue(new Response("nope", { status: 401 }));
    globalThis.fetch = fetchMock;

    await expect(postOpenclawWebhook("https://x/y", { method: "GET" })).rejects.toThrow(
      "webhook failed (401)",
    );
    expect(fetchMock).toHaveBeenCalledTimes(1);
    vi.useFakeTimers();
  });

  it("retries on HTTP 429", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(new Response("rl", { status: 429 }))
      .mockResolvedValueOnce(jsonResponse({ message: { id: "r" } }, 200));
    globalThis.fetch = fetchMock;

    const promise = postWebhookMessage(account, "sk", { text: "t", message_id: "m" });
    await vi.runAllTimersAsync();
    const result = await promise;
    expect(result.messageId).toBe("r");
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("retries on network error", async () => {
    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(new Error("network down"))
      .mockResolvedValueOnce(jsonResponse({ message: { id: "n" } }, 200));
    globalThis.fetch = fetchMock;

    const promise = postWebhookMessage(account, "sk", { text: "t", message_id: "m" });
    await vi.runAllTimersAsync();
    const result = await promise;
    expect(result.messageId).toBe("n");
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("throws after all retries exhausted on persistent 500", async () => {
    vi.useRealTimers();
    const fetchMock = vi.fn().mockResolvedValue(new Response("e", { status: 500 }));
    globalThis.fetch = fetchMock;

    await expect(postOpenclawWebhook("https://x/y", { method: "GET" })).rejects.toThrow(
      "webhook failed (500)",
    );
    expect(fetchMock).toHaveBeenCalledTimes(WEBHOOK_MAX_ATTEMPTS);
    vi.useFakeTimers();
  });

  it("uses payload message_id when JSON parse fails", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response("not json", { status: 200, headers: { "Content-Type": "text/plain" } }),
    );
    globalThis.fetch = fetchMock;

    const promise = postWebhookMessage(account, "sk", {
      text: "t",
      message_id: "fallback-mid",
    });
    await vi.runAllTimersAsync();
    const result = await promise;
    expect(result.messageId).toBe("fallback-mid");
  });
});

describe("resolveOutboundMediaUrl", () => {
  const originalFetch = globalThis.fetch;

  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it.each([
    ["bare local path", "/home/node/.openclaw/media/gen/cat.png", "/home/node/.openclaw/media/gen/cat.png"],
    ["file:// path", "file:///tmp/shot.png", "/tmp/shot.png"],
    ["path with surrounding whitespace", "  /tmp/shot.png  ", "/tmp/shot.png"],
  ])("uploads a %s through the agent media proxy", async (_label, input, expectedLocalPath) => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        file_id: "f1",
        filename: "cat.png",
        content_type: "image/png",
        size_bytes: 10,
        download_url: "https://cloud.example/files/f1/cat.png",
        expires_at: "2099-01-01T00:00:00Z",
      }),
    );
    globalThis.fetch = fetchMock;

    const promise = resolveOutboundMediaUrl(account, input);
    await vi.runAllTimersAsync();
    const result = await promise;

    expect(result).toEqual({
      url: "https://cloud.example/files/f1/cat.png",
      contentType: "image/png",
    });
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("http://127.0.0.1:8001/internal/openclaw/media/upload-local");
    expect(JSON.parse(init.body as string)).toEqual({ local_path: expectedLocalPath });
  });

  it("passes an http(s) URL through unchanged without uploading", async () => {
    const fetchMock = vi.fn();
    globalThis.fetch = fetchMock;

    const result = await resolveOutboundMediaUrl(account, "  https://cdn.example/pic.webp  ");

    expect(result).toEqual({ url: "https://cdn.example/pic.webp", contentType: "" });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("throws on an empty source", async () => {
    const fetchMock = vi.fn();
    globalThis.fetch = fetchMock;

    await expect(resolveOutboundMediaUrl(account, "   ")).rejects.toThrow(
      "empty media source",
    );
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("postWebhookMediaMessage", () => {
  const originalFetch = globalThis.fetch;

  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  async function capturePostedBody(
    params: Parameters<typeof postWebhookMediaMessage>[2],
  ): Promise<Record<string, unknown>> {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ message: { id: "m1" } }));
    globalThis.fetch = fetchMock;
    const promise = postWebhookMediaMessage(account, "sk", params);
    await vi.runAllTimersAsync();
    await promise;
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    return JSON.parse(init.body as string) as Record<string, unknown>;
  }

  it.each([
    [
      "content-type image/* with an extensionless URL",
      "image/png",
      "https://cdn.example/files/abc",
      "image_url",
    ],
    ["empty content-type but an image extension", "", "https://cdn.example/shot.jpeg", "image_url"],
    ["an image extension followed by a query string", "", "https://cdn.example/x.png?v=2", "image_url"],
    ["a non-image content-type", "application/pdf", "https://cdn.example/report.pdf", "file_url"],
    ["empty content-type and a non-image extension", "", "https://cdn.example/data.csv", "file_url"],
  ])("renders %s as a %s raw_content block", async (_label, contentType, mediaUrl, expectedType) => {
    const body = await capturePostedBody({
      mediaUrl,
      contentType,
      caption: "look",
      chatId: "c1",
      messageId: "mid-1",
    });
    expect(body.raw_content).toEqual([
      { type: "text", text: "look" },
      expectedType === "image_url"
        ? { type: "image_url", image_url: { url: mediaUrl } }
        : { type: "file_url", file_url: { url: mediaUrl } },
    ]);
  });

  it("includes the caption as a text block and as the message text", async () => {
    const body = await capturePostedBody({
      mediaUrl: "https://cdn.example/x.png",
      contentType: "image/png",
      caption: "here you go",
      chatId: "c1",
      messageId: "mid-2",
    });
    expect(body.text).toBe("here you go");
    expect((body.raw_content as unknown[])[0]).toEqual({ type: "text", text: "here you go" });
    expect(body.chat_id).toBe("c1");
    expect(body.message_id).toBe("mid-2");
  });

  it("drops the text block and uses a blank placeholder text for a blank caption", async () => {
    // ``text`` must pass backend min_length=1; we send a single space so the
    // bubble renders image-only instead of leaking the cloud URL.
    const body = await capturePostedBody({
      mediaUrl: "https://cdn.example/x.png",
      contentType: "image/png",
      caption: "   ",
      chatId: "c1",
      messageId: "mid-3",
    });
    expect(body.raw_content).toEqual([
      { type: "image_url", image_url: { url: "https://cdn.example/x.png" } },
    ]);
    expect(body.text).toBe(" ");
  });

  it("omits chat_id when chatId is null", async () => {
    const body = await capturePostedBody({
      mediaUrl: "https://cdn.example/x.png",
      contentType: "image/png",
      caption: "cap",
      chatId: null,
      messageId: "mid-4",
    });
    expect(body).not.toHaveProperty("chat_id");
  });
});

type PluginOutbound = {
  outbound: {
    sendText: (p: unknown) => Promise<{ messageId: string }>;
    sendImage: (p: unknown) => Promise<{ messageId: string }>;
    attachedResults: {
      sendText: (p: unknown) => Promise<{ messageId: string }>;
      sendImage: (p: unknown) => Promise<{ messageId: string }>;
    };
  };
};

describe("sendText empty text skip", () => {
  const originalFetch = globalThis.fetch;

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  it("returns empty without calling fetch", async () => {
    const fetchMock = vi.fn();
    globalThis.fetch = fetchMock;

    const plugin = sellerclawUiChannelPlugin as PluginOutbound;
    const result = await plugin.outbound.attachedResults.sendText({
      account,
      sessionKey: "sk",
      text: "   ",
    });

    expect(result).toEqual({ messageId: "empty" });
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("sendText resolves account from stored plugin config", () => {
  const originalFetch = globalThis.fetch;

  afterEach(() => {
    globalThis.fetch = originalFetch;
    setPluginConfig(null as unknown as OpenClawConfig);
  });

  it("falls back to plugin config when params lack account and config", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({ message: { id: "cfg-fallback" } }, 200),
    );
    globalThis.fetch = fetchMock;

    setPluginConfig({
      channels: {
        "sellerclaw-ui": {
          apiBaseUrl: "https://api.example.com",
          userId: account.userId,
          agentApiKey: account.agentApiKey,
          internalWebhookSecret: account.internalWebhookSecret,
        },
      },
    } as OpenClawConfig);

    const plugin = sellerclawUiChannelPlugin as PluginOutbound;
    const result = await plugin.outbound.attachedResults.sendText({
      sessionKey: "agent:supervisor:sellerclaw-ui:direct:abc",
      text: "hello from subagent",
    });

    expect(result).toEqual({ messageId: "cfg-fallback" });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("throws when no account, no config, and no plugin config stored", async () => {
    setPluginConfig(null as unknown as OpenClawConfig);

    const plugin = sellerclawUiChannelPlugin as PluginOutbound;
    await expect(
      plugin.outbound.attachedResults.sendText({
        sessionKey: "sk",
        text: "fail",
      }),
    ).rejects.toThrow("sellerclaw-ui: missing account/config in outbound params");
  });
});

describe("extractChatIdFromAddress", () => {
  it.each([
    ["sellerclaw-ui:direct:550e8400-e29b-41d4-a716-446655440000", "550e8400-e29b-41d4-a716-446655440000"],
    ["sellerclaw-ui:direct:7A746768-528A-4989-822F-7027DAF74C63", "7A746768-528A-4989-822F-7027DAF74C63"],
  ])("extracts UUID from %s", (address, expected) => {
    expect(extractChatIdFromAddress(address)).toBe(expected);
  });

  it.each([
    "sellerclaw-ui:agent",
    "agent:supervisor:sellerclaw-ui:direct:550e8400-e29b-41d4-a716-446655440000",
    "direct:550e8400-e29b-41d4-a716-446655440000",
    "sellerclaw-ui:direct:not-a-uuid",
    "",
  ])("returns null for %s", (address) => {
    expect(extractChatIdFromAddress(address)).toBeNull();
  });
});

describe("top-level outbound sendText", () => {
  const originalFetch = globalThis.fetch;

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  it("sends via top-level outbound.sendText (proactive path)", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({ message: { id: "proactive-1" } }, 200),
    );
    globalThis.fetch = fetchMock;

    const plugin = sellerclawUiChannelPlugin as PluginOutbound;
    const result = await plugin.outbound.sendText({
      account,
      sessionKey: "agent:supervisor:sellerclaw-ui:direct:abc",
      text: "proactive message",
    });

    expect(result).toEqual({ messageId: "proactive-1" });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("includes chat_id when to is a channel address with UUID", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({ message: { id: "announce-1" } }, 200),
    );
    globalThis.fetch = fetchMock;

    const plugin = sellerclawUiChannelPlugin as PluginOutbound;
    const result = await plugin.outbound.sendText({
      account,
      to: "sellerclaw-ui:direct:550e8400-e29b-41d4-a716-446655440000",
      text: "subagent result",
    });

    expect(result).toEqual({ messageId: "announce-1" });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    const body = JSON.parse(init.body as string) as Record<string, unknown>;
    expect(body.chat_id).toBe("550e8400-e29b-41d4-a716-446655440000");
    expect(body.session_key).toBe("sellerclaw-ui:direct:550e8400-e29b-41d4-a716-446655440000");
  });

  it("does not include chat_id when sessionKey is a full session key", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({ message: { id: "direct-1" } }, 200),
    );
    globalThis.fetch = fetchMock;

    const plugin = sellerclawUiChannelPlugin as PluginOutbound;
    const result = await plugin.outbound.sendText({
      account,
      sessionKey: "agent:supervisor:sellerclaw-ui:direct:550e8400-e29b-41d4-a716-446655440000",
      text: "reply message",
    });

    expect(result).toEqual({ messageId: "direct-1" });
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    const body = JSON.parse(init.body as string) as Record<string, unknown>;
    expect(body.chat_id).toBeUndefined();
  });

  it("returns empty for whitespace-only text", async () => {
    const fetchMock = vi.fn();
    globalThis.fetch = fetchMock;

    const plugin = sellerclawUiChannelPlugin as PluginOutbound;
    const result = await plugin.outbound.sendText({
      account,
      sessionKey: "sk",
      text: "  \n  ",
    });

    expect(result).toEqual({ messageId: "empty" });
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
