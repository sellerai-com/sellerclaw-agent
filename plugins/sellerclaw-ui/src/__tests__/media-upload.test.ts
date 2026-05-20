import "./sdk-mock.js";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { sellerclawUiChannelPlugin } from "../channel.js";
import { type ScwUiAccount, uploadLocalMedia } from "../send.js";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const account: ScwUiAccount = {
  apiBaseUrl: "https://api.example.com",
  userId: "550e8400-e29b-41d4-a716-446655440000",
  agentApiKey: "sca",
  internalWebhookSecret: "hooks-token",
  localAgentBaseUrl: "http://127.0.0.1:8001",
};

type PluginOutbound = {
  outbound: {
    sendImage: (p: unknown) => Promise<{ messageId: string }>;
    sendMedia: (p: unknown) => Promise<{ messageId: string }>;
  };
};

const SESSION_KEY = "sellerclaw-ui:direct:550e8400-e29b-41d4-a716-446655440000";

describe("uploadLocalMedia", () => {
  const originalFetch = globalThis.fetch;

  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("POSTs local_path to agent proxy with hooks bearer and returns download_url", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        file_id: "fid-1",
        filename: "shot.png",
        content_type: "image/png",
        size_bytes: 42,
        download_url: "https://cloud.example/files/fid-1/shot.png",
        expires_at: "2099-01-01T00:00:00Z",
      }),
    );
    globalThis.fetch = fetchMock;

    const promise = uploadLocalMedia(account, "/home/node/.openclaw/media/shot.png");
    await vi.runAllTimersAsync();
    const result = await promise;

    expect(result.downloadUrl).toBe("https://cloud.example/files/fid-1/shot.png");
    expect(result.filename).toBe("shot.png");
    expect(result.contentType).toBe("image/png");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("http://127.0.0.1:8001/internal/openclaw/media/upload-local");
    expect(init.method).toBe("POST");
    expect(init.headers).toMatchObject({
      "Content-Type": "application/json",
      Authorization: "Bearer hooks-token",
    });
    const body = JSON.parse(init.body as string) as Record<string, unknown>;
    expect(body).toEqual({ local_path: "/home/node/.openclaw/media/shot.png" });
  });

  it("throws when localAgentBaseUrl is empty", async () => {
    const fetchMock = vi.fn();
    globalThis.fetch = fetchMock;
    const bad: ScwUiAccount = { ...account, localAgentBaseUrl: "" };
    await expect(uploadLocalMedia(bad, "/home/node/foo.png")).rejects.toThrow(
      "localAgentBaseUrl is required",
    );
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("throws when response lacks download_url", async () => {
    vi.useRealTimers();
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ file_id: "f" }));
    globalThis.fetch = fetchMock;
    await expect(
      uploadLocalMedia(account, "/home/node/foo.png"),
    ).rejects.toThrow("missing download_url");
    vi.useFakeTimers();
  });
});

describe("outboundSendImage auto-uploads local paths", () => {
  const originalFetch = globalThis.fetch;

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("uploads when imagePath is provided and forwards download_url to webhook", async () => {
    const uploadResponse = jsonResponse({
      file_id: "f1",
      filename: "shot.png",
      content_type: "image/png",
      size_bytes: 11,
      download_url: "https://cloud.example/files/f1/shot.png",
      expires_at: "2099-01-01T00:00:00Z",
    });
    const webhookResponse = jsonResponse({ message: { id: "m-ok" } });
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(uploadResponse)
      .mockResolvedValueOnce(webhookResponse);
    globalThis.fetch = fetchMock;

    const plugin = sellerclawUiChannelPlugin as PluginOutbound;
    const result = await plugin.outbound.sendImage({
      account,
      sessionKey: "sellerclaw-ui:direct:550e8400-e29b-41d4-a716-446655440000",
      text: "here is the page",
      imagePath: "/home/node/.openclaw/media/browser/abc.jpg",
    });

    expect(result).toEqual({ messageId: "m-ok" });
    expect(fetchMock).toHaveBeenCalledTimes(2);

    const [uploadUrl, uploadInit] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(uploadUrl).toBe("http://127.0.0.1:8001/internal/openclaw/media/upload-local");
    expect(JSON.parse(uploadInit.body as string)).toEqual({
      local_path: "/home/node/.openclaw/media/browser/abc.jpg",
    });

    const [webhookUrl, webhookInit] = fetchMock.mock.calls[1] as [string, RequestInit];
    expect(webhookUrl).toBe("https://api.example.com/internal/openclaw/messages");
    const body = JSON.parse(webhookInit.body as string) as {
      raw_content: Array<{ type: string; image_url?: { url: string } }>;
      text: string;
    };
    const image = body.raw_content.find((e) => e.type === "image_url");
    expect(image?.image_url?.url).toBe("https://cloud.example/files/f1/shot.png");
    expect(body.text).toBe("here is the page");
  });

  it("uploads when imageUrl is a local path", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse({
          file_id: "f2",
          filename: "x.png",
          content_type: "image/png",
          size_bytes: 1,
          download_url: "https://cloud.example/files/f2/x.png",
          expires_at: "z",
        }),
      )
      .mockResolvedValueOnce(jsonResponse({ message: { id: "m2" } }));
    globalThis.fetch = fetchMock;

    const plugin = sellerclawUiChannelPlugin as PluginOutbound;
    await plugin.outbound.sendImage({
      account,
      sessionKey: "sellerclaw-ui:direct:550e8400-e29b-41d4-a716-446655440000",
      imageUrl: "/tmp/snapshot.png",
    });

    const [uploadUrl, uploadInit] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(uploadUrl).toBe("http://127.0.0.1:8001/internal/openclaw/media/upload-local");
    expect(JSON.parse(uploadInit.body as string)).toEqual({
      local_path: "/tmp/snapshot.png",
    });
  });

  it("strips file:// prefix before upload", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse({
          file_id: "f3",
          filename: "x.png",
          content_type: "image/png",
          size_bytes: 1,
          download_url: "https://cloud.example/files/f3/x.png",
          expires_at: "z",
        }),
      )
      .mockResolvedValueOnce(jsonResponse({ message: { id: "m3" } }));
    globalThis.fetch = fetchMock;

    const plugin = sellerclawUiChannelPlugin as PluginOutbound;
    await plugin.outbound.sendImage({
      account,
      sessionKey: "sellerclaw-ui:direct:550e8400-e29b-41d4-a716-446655440000",
      imagePath: "file:///home/node/.openclaw/media/abc.png",
    });

    const [, uploadInit] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(JSON.parse(uploadInit.body as string)).toEqual({
      local_path: "/home/node/.openclaw/media/abc.png",
    });
  });

  it("does not upload when imageUrl is already HTTPS", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse({ message: { id: "m-direct" } }));
    globalThis.fetch = fetchMock;

    const plugin = sellerclawUiChannelPlugin as PluginOutbound;
    const result = await plugin.outbound.sendImage({
      account,
      sessionKey: "sellerclaw-ui:direct:550e8400-e29b-41d4-a716-446655440000",
      imageUrl: "https://cdn.example/already-public.png",
    });

    expect(result).toEqual({ messageId: "m-direct" });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("https://api.example.com/internal/openclaw/messages");
  });

  it("throws when neither imageUrl nor imagePath is provided", async () => {
    const fetchMock = vi.fn();
    globalThis.fetch = fetchMock;

    const plugin = sellerclawUiChannelPlugin as PluginOutbound;
    await expect(
      plugin.outbound.sendImage({
        account,
        sessionKey: "sellerclaw-ui:direct:550e8400-e29b-41d4-a716-446655440000",
      }),
    ).rejects.toThrow("imageUrl or imagePath is required");
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

/**
 * `outbound.sendMedia` is the path OpenClaw's normalized outbound deliverer uses
 * (incl. the requester-agent handoff that delivers background `image_generate`
 * results). The runtime calls it with `{ text: caption, mediaUrl }`.
 */
describe("outboundSendMedia delivers media via the outbound deliver pipeline", () => {
  const originalFetch = globalThis.fetch;

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("uploads a local mediaUrl and delivers it as an image part with the caption", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse({
          file_id: "f1",
          filename: "cat.png",
          content_type: "image/png",
          size_bytes: 11,
          download_url: "https://cloud.example/files/f1/cat.png",
          expires_at: "2099-01-01T00:00:00Z",
        }),
      )
      .mockResolvedValueOnce(jsonResponse({ message: { id: "m-media" } }));
    globalThis.fetch = fetchMock;

    const plugin = sellerclawUiChannelPlugin as PluginOutbound;
    const result = await plugin.outbound.sendMedia({
      account,
      to: SESSION_KEY,
      text: "Here is the blue Cornish Rex.",
      mediaUrl: "/home/node/.openclaw/media/tool-image-generation/cat.png",
    });

    expect(result).toEqual({ messageId: "m-media" });
    expect(fetchMock).toHaveBeenCalledTimes(2);

    const [uploadUrl, uploadInit] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(uploadUrl).toBe("http://127.0.0.1:8001/internal/openclaw/media/upload-local");
    expect(JSON.parse(uploadInit.body as string)).toEqual({
      local_path: "/home/node/.openclaw/media/tool-image-generation/cat.png",
    });

    const [webhookUrl, webhookInit] = fetchMock.mock.calls[1] as [string, RequestInit];
    expect(webhookUrl).toBe("https://api.example.com/internal/openclaw/messages");
    const body = JSON.parse(webhookInit.body as string) as {
      raw_content: Array<{ type: string; text?: string; image_url?: { url: string } }>;
      text: string;
      chat_id: string;
    };
    expect(body.text).toBe("Here is the blue Cornish Rex.");
    expect(body.chat_id).toBe("550e8400-e29b-41d4-a716-446655440000");
    expect(body.raw_content).toContainEqual({
      type: "text",
      text: "Here is the blue Cornish Rex.",
    });
    expect(body.raw_content).toContainEqual({
      type: "image_url",
      image_url: { url: "https://cloud.example/files/f1/cat.png" },
    });
  });

  it("passes through a public https image URL without uploading", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(jsonResponse({ message: { id: "m2" } }));
    globalThis.fetch = fetchMock;

    const plugin = sellerclawUiChannelPlugin as PluginOutbound;
    await plugin.outbound.sendMedia({
      account,
      to: SESSION_KEY,
      mediaUrl: "https://cdn.example/already-public.png",
    });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [webhookUrl, webhookInit] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(webhookUrl).toBe("https://api.example.com/internal/openclaw/messages");
    const body = JSON.parse(webhookInit.body as string) as {
      raw_content: Array<{ type: string; image_url?: { url: string } }>;
      text: string;
    };
    // No caption → a single space keeps backend `text` validation happy while
    // rendering as an image-only bubble.
    expect(body.text).toBe(" ");
    expect(body.raw_content).toEqual([
      { type: "image_url", image_url: { url: "https://cdn.example/already-public.png" } },
    ]);
  });

  it("delivers a non-image URL as a file part", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(jsonResponse({ message: { id: "m3" } }));
    globalThis.fetch = fetchMock;

    const plugin = sellerclawUiChannelPlugin as PluginOutbound;
    await plugin.outbound.sendMedia({
      account,
      to: SESSION_KEY,
      text: "Your report",
      mediaUrl: "https://cdn.example/report.pdf",
    });

    const [, webhookInit] = fetchMock.mock.calls[0] as [string, RequestInit];
    const body = JSON.parse(webhookInit.body as string) as {
      raw_content: Array<{ type: string; file_url?: { url: string } }>;
    };
    expect(body.raw_content).toContainEqual({
      type: "file_url",
      file_url: { url: "https://cdn.example/report.pdf" },
    });
  });

  it("throws when mediaUrl is missing", async () => {
    const fetchMock = vi.fn();
    globalThis.fetch = fetchMock;

    const plugin = sellerclawUiChannelPlugin as PluginOutbound;
    await expect(
      plugin.outbound.sendMedia({ account, to: SESSION_KEY, text: "no media" }),
    ).rejects.toThrow("mediaUrl is required");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("does not deliver when the silent flag is set", async () => {
    const fetchMock = vi.fn();
    globalThis.fetch = fetchMock;

    const plugin = sellerclawUiChannelPlugin as PluginOutbound;
    const result = await plugin.outbound.sendMedia({
      account,
      to: SESSION_KEY,
      mediaUrl: "https://cdn.example/x.png",
      silent: true,
    });

    expect(result).toEqual({ messageId: "silent" });
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
