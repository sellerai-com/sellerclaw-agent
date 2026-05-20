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

/**
 * All outbound sends funnel through the turn/part/end endpoints — each send is its own
 * assistant message. Local artifacts are proxy-uploaded before the media part is posted.
 */
describe("outbound parts pipeline", () => {
  const originalFetch = globalThis.fetch;
  const partsAccount: ScwUiAccount = { ...account };

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("sendText delivers as turn → text part → end", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ ok: true }));
    globalThis.fetch = fetchMock;

    const plugin = sellerclawUiChannelPlugin as {
      outbound: { sendText: (p: unknown) => Promise<{ messageId: string }> };
    };
    await plugin.outbound.sendText({ account: partsAccount, to: SESSION_KEY, text: "hello" });

    expect(fetchMock).toHaveBeenCalledTimes(3);
    const urls = fetchMock.mock.calls.map((c) => c[0] as string);
    expect(urls[0]).toBe("https://api.example.com/internal/openclaw/turn");
    expect(urls[1]).toMatch(/\/internal\/openclaw\/turn\/[0-9a-f-]+\/part$/);
    expect(urls[2]).toMatch(/\/internal\/openclaw\/turn\/[0-9a-f-]+\/end$/);
    const startBody = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string);
    expect(startBody).toMatchObject({ chat_id: "550e8400-e29b-41d4-a716-446655440000" });
    const partBody = JSON.parse((fetchMock.mock.calls[1][1] as RequestInit).body as string);
    expect(partBody).toMatchObject({ kind: "text", text: "hello" });
  });

  it("sendMedia uploads a local path then delivers caption + image parts", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse({
          download_url: "https://cloud.example/f/cat.png",
          content_type: "image/png",
          filename: "cat.png",
        }),
      )
      .mockResolvedValue(jsonResponse({ ok: true }));
    globalThis.fetch = fetchMock;

    const plugin = sellerclawUiChannelPlugin as PluginOutbound;
    await plugin.outbound.sendMedia({
      account: partsAccount,
      to: SESSION_KEY,
      text: "caption",
      mediaUrl: "/home/node/.openclaw/media/cat.png",
    });

    // upload + turn + part(text) + part(image) + end
    expect(fetchMock).toHaveBeenCalledTimes(5);
    const urls = fetchMock.mock.calls.map((c) => c[0] as string);
    expect(urls[0]).toBe("http://127.0.0.1:8001/internal/openclaw/media/upload-local");
    expect(urls[1]).toBe("https://api.example.com/internal/openclaw/turn");
    expect(urls[urls.length - 1]).toMatch(/\/end$/);
    const textPart = JSON.parse((fetchMock.mock.calls[2][1] as RequestInit).body as string);
    expect(textPart).toMatchObject({ kind: "text", text: "caption" });
    const imagePart = JSON.parse((fetchMock.mock.calls[3][1] as RequestInit).body as string);
    expect(imagePart).toMatchObject({
      kind: "image",
      url: "https://cloud.example/f/cat.png",
      content_type: "image/png",
    });
  });

  it("sendImage uploads a local imagePath and delivers an image part", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse({ download_url: "https://cloud.example/f/shot.png", content_type: "image/png" }),
      )
      .mockResolvedValue(jsonResponse({ ok: true }));
    globalThis.fetch = fetchMock;

    const plugin = sellerclawUiChannelPlugin as PluginOutbound;
    await plugin.outbound.sendImage({
      account: partsAccount,
      to: SESSION_KEY,
      text: "here is the page",
      imagePath: "/home/node/.openclaw/media/browser/abc.jpg",
    });

    const urls = fetchMock.mock.calls.map((c) => c[0] as string);
    expect(urls[0]).toBe("http://127.0.0.1:8001/internal/openclaw/media/upload-local");
    expect(JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string)).toEqual({
      local_path: "/home/node/.openclaw/media/browser/abc.jpg",
    });
    expect(urls[1]).toBe("https://api.example.com/internal/openclaw/turn");
    expect(urls[urls.length - 1]).toMatch(/\/end$/);
    const parts = fetchMock.mock.calls
      .slice(2)
      .map((c) => JSON.parse((c[1] as RequestInit).body as string) as Record<string, unknown>);
    expect(parts).toContainEqual(expect.objectContaining({ kind: "text", text: "here is the page" }));
    expect(parts).toContainEqual(
      expect.objectContaining({ kind: "image", url: "https://cloud.example/f/shot.png" }),
    );
  });

  it("sendImage strips a file:// prefix before upload", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse({ download_url: "https://cloud.example/f/x.png", content_type: "image/png" }),
      )
      .mockResolvedValue(jsonResponse({ ok: true }));
    globalThis.fetch = fetchMock;

    const plugin = sellerclawUiChannelPlugin as PluginOutbound;
    await plugin.outbound.sendImage({
      account: partsAccount,
      to: SESSION_KEY,
      imagePath: "file:///home/node/.openclaw/media/abc.png",
    });

    expect(JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string)).toEqual({
      local_path: "/home/node/.openclaw/media/abc.png",
    });
  });

  it("sendImage passes a public https URL straight through (no upload)", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ ok: true }));
    globalThis.fetch = fetchMock;

    const plugin = sellerclawUiChannelPlugin as PluginOutbound;
    await plugin.outbound.sendImage({
      account: partsAccount,
      to: SESSION_KEY,
      imageUrl: "https://cdn.example/already-public.png",
    });

    const urls = fetchMock.mock.calls.map((c) => c[0] as string);
    expect(urls.some((u) => u.includes("upload-local"))).toBe(false);
    expect(urls[0]).toBe("https://api.example.com/internal/openclaw/turn");
    const imagePart = fetchMock.mock.calls
      .map((c) => {
        try {
          return JSON.parse((c[1] as RequestInit).body as string) as Record<string, unknown>;
        } catch {
          return {};
        }
      })
      .find((b) => b.kind === "image");
    expect(imagePart?.url).toBe("https://cdn.example/already-public.png");
  });

  it("sendImage throws when neither imageUrl nor imagePath is provided", async () => {
    const fetchMock = vi.fn();
    globalThis.fetch = fetchMock;

    const plugin = sellerclawUiChannelPlugin as PluginOutbound;
    await expect(
      plugin.outbound.sendImage({ account: partsAccount, to: SESSION_KEY }),
    ).rejects.toThrow("imageUrl or imagePath is required");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("sendMedia delivers a non-image URL as a file part", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ ok: true }));
    globalThis.fetch = fetchMock;

    const plugin = sellerclawUiChannelPlugin as PluginOutbound;
    await plugin.outbound.sendMedia({
      account: partsAccount,
      to: SESSION_KEY,
      mediaUrl: "https://cdn.example/report.pdf",
    });

    const filePart = fetchMock.mock.calls
      .map((c) => {
        try {
          return JSON.parse((c[1] as RequestInit).body as string) as Record<string, unknown>;
        } catch {
          return {};
        }
      })
      .find((b) => b.kind === "file");
    expect(filePart?.url).toBe("https://cdn.example/report.pdf");
  });

  it("sendMedia throws when mediaUrl is missing", async () => {
    const fetchMock = vi.fn();
    globalThis.fetch = fetchMock;

    const plugin = sellerclawUiChannelPlugin as PluginOutbound;
    await expect(
      plugin.outbound.sendMedia({ account: partsAccount, to: SESSION_KEY, text: "no media" }),
    ).rejects.toThrow("mediaUrl is required");
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
