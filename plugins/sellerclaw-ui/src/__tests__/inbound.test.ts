import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { IncomingMessage, ServerResponse } from "node:http";

const { dispatchMock, readBodyMock, postWebhookMock, saveMediaBufferMock } = vi.hoisted(() => ({
  dispatchMock: vi.fn().mockResolvedValue(undefined),
  readBodyMock: vi.fn(),
  postWebhookMock: vi.fn().mockResolvedValue(new Response(null, { status: 200 })),
  saveMediaBufferMock: vi.fn(),
}));

vi.mock("openclaw/plugin-sdk/channel-inbound", () => ({
  dispatchInboundDirectDmWithRuntime: (...args: unknown[]) => dispatchMock(...args),
  runPreparedInboundReply: vi.fn(),
}));

// inbound.ts now dispatches through our local re-implementation (which forwards reasoning-stream
// callbacks); mock it onto the same dispatchMock so the existing arg-capture assertions hold.
vi.mock("../inbound-reply-with-reasoning.js", () => ({
  dispatchInboundDirectDmWithReasoning: (...args: unknown[]) => dispatchMock(...args),
}));

vi.mock("openclaw/plugin-sdk/reply-payload", () => ({
  isReasoningReplyPayload: (payload: Record<string, unknown>) => {
    if (payload?.isReasoning === true) return true;
    const text = typeof payload?.text === "string" ? payload.text : "";
    return /^(?:reasoning:|thinking\.{0,3}(?=\s*(?:>\s*)?_))/iu.test(text.trimStart());
  },
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

import { pickDeltaJoin, readDeliverPayload, registerInboundRoute } from "../inbound.js";

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
  // Trailing default for the turn/part/end posts that finalize the (possibly empty)
  // assistant turn after dispatch — these tests assert on attachment ingest, not delivery.
  fetchMock.mockResolvedValue({
    ok: true,
    status: 200,
    headers: { get: () => "application/json" },
    arrayBuffer: async () => new ArrayBuffer(0),
  });
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

  it("funnels deliver text through the turn/part endpoints", async () => {
    // The turn helpers live inside send.ts and call the real postOpenclawWebhook →
    // global fetch (the partial send.js mock only patches inbound.ts's import), so we
    // assert on a fetch mock here rather than postWebhookMock.
    const originalFetch = globalThis.fetch;
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 200 }));
    globalThis.fetch = fetchMock as unknown as typeof fetch;
    try {
      readBodyMock.mockResolvedValue({
        ok: true,
        value: { chat_id: "c1", agent_id: "supervisor", user_id: "u1", text: " hi " },
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

      const req = { headers: { authorization: "Bearer secret" } } as IncomingMessage;
      const res = { statusCode: 0, end: vi.fn() } as unknown as ServerResponse;

      await handler(req, res);
      const arg = dispatchMock.mock.calls[0]![0] as {
        deliver: (p: { text: string }) => Promise<void>;
      };
      await arg.deliver({ text: "chunk" });

      const urls = fetchMock.mock.calls.map((c) => String(c[0]));
      expect(urls.some((u) => u.endsWith("/internal/openclaw/turn"))).toBe(true);
      expect(urls.some((u) => /\/internal\/openclaw\/turn\/[0-9a-f-]+\/part$/.test(u))).toBe(true);
      // New protocol replaces the legacy text-only road.
      expect(urls.some((u) => u.includes("/stream-delta"))).toBe(false);

      const partCall = fetchMock.mock.calls.find((c) =>
        /\/internal\/openclaw\/turn\/[0-9a-f-]+\/part$/.test(String(c[0])),
      )!;
      const partBody = JSON.parse(
        String((partCall[1] as RequestInit).body),
      ) as Record<string, string>;
      expect(partBody).toMatchObject({
        kind: "text",
        text: "chunk",
        session_key: "agent:supervisor:sellerclaw-ui:direct:c1",
        chat_id: "c1",
      });

      await vi.waitFor(() => {
        const endCalls = fetchMock.mock.calls.filter((c) =>
          /\/internal\/openclaw\/turn\/[0-9a-f-]+\/end$/.test(String(c[0])),
        );
        expect(endCalls.length).toBeGreaterThanOrEqual(1);
      });
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("routes reasoning payload to /internal/openclaw/thought, not /part", async () => {
    const originalFetch = globalThis.fetch;
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    globalThis.fetch = fetchMock as unknown as typeof fetch;
    try {
      readBodyMock.mockResolvedValue({
        ok: true,
        value: { chat_id: "c1", agent_id: "supervisor", user_id: "u1", text: "hi" },
      });

      const { api, registerHttpRoute } = buildApi();
      registerInboundRoute(api as import("openclaw/plugin-sdk/core").OpenClawPluginApi);
      const handler = getHandler(registerHttpRoute);

      const req = { headers: { authorization: "Bearer secret" } } as IncomingMessage;
      const res = { statusCode: 0, end: vi.fn() } as unknown as ServerResponse;
      await handler(req, res);

      const arg = dispatchMock.mock.calls[0]![0] as {
        deliver: (p: Record<string, unknown>) => Promise<void>;
      };
      await arg.deliver({ text: "weighing the trade-offs…", isReasoning: true });

      const urls = fetchMock.mock.calls.map((c) => String(c[0]));
      const thoughtCall = fetchMock.mock.calls.find((c) =>
        String(c[0]).endsWith("/internal/openclaw/thought"),
      );
      expect(thoughtCall).toBeDefined();
      expect(urls.some((u) => /\/internal\/openclaw\/turn\/[0-9a-f-]+\/part$/.test(u))).toBe(false);

      const body = JSON.parse(
        String((thoughtCall![1] as RequestInit).body),
      ) as Record<string, unknown>;
      expect(body).toMatchObject({
        kind: "text",
        text: "weighing the trade-offs…",
        agent_id: "supervisor",
        seq: 0,
        session_key: "agent:supervisor:sellerclaw-ui:direct:c1",
        chat_id: "c1",
      });
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("streams reasoning via replyOptions.onReasoningStream and posts a thought when the block closes", async () => {
    const originalFetch = globalThis.fetch;
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    globalThis.fetch = fetchMock as unknown as typeof fetch;
    try {
      readBodyMock.mockResolvedValue({
        ok: true,
        value: { chat_id: "c1", agent_id: "supervisor", user_id: "u1", text: "hi" },
      });

      const { api, registerHttpRoute } = buildApi();
      registerInboundRoute(api as import("openclaw/plugin-sdk/core").OpenClawPluginApi);
      const handler = getHandler(registerHttpRoute);

      const req = { headers: { authorization: "Bearer secret" } } as IncomingMessage;
      const res = { statusCode: 0, end: vi.fn() } as unknown as ServerResponse;
      await handler(req, res);

      const arg = dispatchMock.mock.calls[0]![0] as {
        replyOptions?: {
          onReasoningStream?: (e: { text?: string }) => void;
          onReasoningEnd?: () => void;
        };
      };
      expect(arg.replyOptions?.onReasoningStream).toBeTypeOf("function");
      expect(arg.replyOptions?.onReasoningEnd).toBeTypeOf("function");

      // OpenClaw streams the CUMULATIVE reasoning text; the plugin diffs it to a delta and
      // accumulates, posting nothing until the reasoning block closes.
      arg.replyOptions!.onReasoningStream!({ text: "Let me think." });
      arg.replyOptions!.onReasoningStream!({ text: "Let me think. Step two." });
      expect(
        fetchMock.mock.calls.some((c) => String(c[0]).endsWith("/internal/openclaw/thought")),
      ).toBe(false);

      arg.replyOptions!.onReasoningEnd!();
      // postThought is fire-and-forget — let its microtask reach the fetch call.
      await new Promise((resolve) => setTimeout(resolve, 0));

      const thoughtCalls = fetchMock.mock.calls.filter((c) =>
        String(c[0]).endsWith("/internal/openclaw/thought"),
      );
      expect(thoughtCalls).toHaveLength(1);
      const body = JSON.parse(
        String((thoughtCalls[0]![1] as RequestInit).body),
      ) as Record<string, unknown>;
      expect(body).toMatchObject({
        kind: "text",
        text: "Let me think. Step two.",
        agent_id: "supervisor",
        seq: 0,
        session_key: "agent:supervisor:sellerclaw-ui:direct:c1",
        chat_id: "c1",
      });
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("does not post a thought when the reasoning payload has empty text", async () => {
    const originalFetch = globalThis.fetch;
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    globalThis.fetch = fetchMock as unknown as typeof fetch;
    try {
      readBodyMock.mockResolvedValue({
        ok: true,
        value: { chat_id: "c1", agent_id: "supervisor", user_id: "u1", text: "hi" },
      });

      const { api, registerHttpRoute } = buildApi();
      registerInboundRoute(api as import("openclaw/plugin-sdk/core").OpenClawPluginApi);
      const handler = getHandler(registerHttpRoute);

      const req = { headers: { authorization: "Bearer secret" } } as IncomingMessage;
      const res = { statusCode: 0, end: vi.fn() } as unknown as ServerResponse;
      await handler(req, res);

      const arg = dispatchMock.mock.calls[0]![0] as {
        deliver: (p: Record<string, unknown>) => Promise<void>;
      };
      await arg.deliver({ text: "   ", isReasoning: true });

      const urls = fetchMock.mock.calls.map((c) => String(c[0]));
      expect(urls.some((u) => u.endsWith("/internal/openclaw/thought"))).toBe(false);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("ends the turn as failed when the dispatch rejects (crash/abort/timeout)", async () => {
    const originalFetch = globalThis.fetch;
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 200 }));
    globalThis.fetch = fetchMock as unknown as typeof fetch;
    try {
      readBodyMock.mockResolvedValue({
        ok: true,
        value: { chat_id: "c1", agent_id: "supervisor", user_id: "u1", text: "hi" },
      });
      dispatchMock.mockRejectedValueOnce(new Error("model run aborted"));

      const { api, registerHttpRoute } = buildApi();
      registerInboundRoute(api as import("openclaw/plugin-sdk/core").OpenClawPluginApi);
      const handler = getHandler(registerHttpRoute);

      const req = { headers: { authorization: "Bearer secret" } } as IncomingMessage;
      const res = { statusCode: 0, end: vi.fn() } as unknown as ServerResponse;
      await handler(req, res);

      await vi.waitFor(() => {
        const endCall = fetchMock.mock.calls.find((c) =>
          /\/internal\/openclaw\/turn\/[0-9a-f-]+\/end$/.test(String(c[0])),
        );
        expect(endCall).toBeDefined();
        const body = JSON.parse(
          String((endCall![1] as RequestInit).body),
        ) as Record<string, string>;
        expect(body.status).toBe("failed");
      });
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("ends an empty but successful dispatch as completed (benign NO_REPLY stays silent)", async () => {
    const originalFetch = globalThis.fetch;
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 200 }));
    globalThis.fetch = fetchMock as unknown as typeof fetch;
    try {
      readBodyMock.mockResolvedValue({
        ok: true,
        value: { chat_id: "c1", agent_id: "supervisor", user_id: "u1", text: "hi" },
      });
      // Dispatch resolves without ever invoking ``deliver`` — no parts streamed.
      dispatchMock.mockResolvedValueOnce(undefined);

      const { api, registerHttpRoute } = buildApi();
      registerInboundRoute(api as import("openclaw/plugin-sdk/core").OpenClawPluginApi);
      const handler = getHandler(registerHttpRoute);

      const req = { headers: { authorization: "Bearer secret" } } as IncomingMessage;
      const res = { statusCode: 0, end: vi.fn() } as unknown as ServerResponse;
      await handler(req, res);

      await vi.waitFor(() => {
        const endCall = fetchMock.mock.calls.find((c) =>
          /\/internal\/openclaw\/turn\/[0-9a-f-]+\/end$/.test(String(c[0])),
        );
        expect(endCall).toBeDefined();
        const body = JSON.parse(
          String((endCall![1] as RequestInit).body),
        ) as Record<string, string>;
        expect(body.status).toBe("completed");
      });
      // No part was streamed: only turn-start and turn-end fetches, no /part.
      const partCalls = fetchMock.mock.calls.filter((c) =>
        /\/internal\/openclaw\/turn\/[0-9a-f-]+\/part$/.test(String(c[0])),
      );
      expect(partCalls).toHaveLength(0);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("funnels deliver media through the turn/part endpoints as an image part", async () => {
    const originalFetch = globalThis.fetch;
    const fetchMock = vi.fn((url: string, _init?: RequestInit) => {
      const u = String(url);
      if (u.includes("/internal/openclaw/media/upload-local")) {
        return Promise.resolve(
          new Response(
            JSON.stringify({ download_url: "https://cloud.example/f/cat.png", content_type: "image/png" }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          ),
        );
      }
      return Promise.resolve(new Response(null, { status: 200 }));
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;
    try {
      readBodyMock.mockResolvedValue({
        ok: true,
        value: { chat_id: "c1", agent_id: "supervisor", user_id: "u1", text: "go" },
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
      const req = { headers: { authorization: "Bearer secret" } } as IncomingMessage;
      const res = { statusCode: 0, end: vi.fn() } as unknown as ServerResponse;
      await handler(req, res);
      const deliver = (
        dispatchMock.mock.calls[0]![0] as {
          deliver: (p: Record<string, unknown>) => Promise<void>;
        }
      ).deliver;
      await deliver({
        text: "Here is the cat",
        mediaUrls: ["/home/node/.openclaw/media/tool-image-generation/cat.png"],
      });

      // Local artifact proxy-uploaded, then delivered as an ordered image part (no /messages).
      const urls = fetchMock.mock.calls.map((c) => String(c[0]));
      expect(urls.some((u) => u.includes("/internal/openclaw/media/upload-local"))).toBe(true);
      expect(urls.some((u) => u.includes("/internal/openclaw/messages"))).toBe(false);
      const imagePart = fetchMock.mock.calls
        .filter((c) => /\/internal\/openclaw\/turn\/[0-9a-f-]+\/part$/.test(String(c[0])))
        .map((c) => JSON.parse(String((c[1] as RequestInit).body)) as Record<string, unknown>)
        .find((b) => b.kind === "image");
      expect(imagePart?.url).toBe("https://cloud.example/f/cat.png");
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  // The OpenClaw block-streaming chunker cuts the assistant reply at internal
  // boundaries and drops the whitespace it cut on. The plugin must reinsert a
  // joiner so the concatenation on the SellerClaw side is valid markdown.
  //
  // Two failure modes we have to avoid:
  //   1. Always-`\n\n` introduces paragraph breaks in the middle of sentences
  //      when the chunker fell back to whitespace cuts (e.g. "несколько\n\n
  //      часов"). Visually disastrous.
  //   2. Always-`" "` collapses real structural elements: heading/list/fence
  //      markers get absorbed into the previous paragraph; closing code fences
  //      stay open and swallow the rest of the message.
  //
  // `pickDeltaJoin` picks `\n\n` only when the surrounding context contains a
  // markdown block-starter, and uses a space otherwise.
  describe("pickDeltaJoin", () => {
    it("returns empty string for the very first delta (no prev tail)", () => {
      expect(pickDeltaJoin("", "Hello world")).toBe("");
    });

    it.each([
      ["heading", "Hello.", "## Захват Дании"],
      ["fenced code opening", "Here is the code:", "```python\nprint('x')"],
      ["bullet list (`- `)", "...as follows:", "- first item"],
      ["bullet list (`* `)", "...as follows:", "* first item"],
      ["bullet list (`+ `)", "...as follows:", "+ first item"],
      ["ordered list (`N. `)", "Reasons:", "1. Strategy"],
      ["ordered list (`N) `)", "Reasons:", "1) Strategy"],
      ["blockquote", "He said:", "> a quoted sentence"],
      ["thematic break (---)", "Section over.", "---"],
      ["thematic break (***)", "Section over.", "***"],
      ["table row", "Data:", "| col1 | col2 |"],
    ])("inserts `\\n\\n` when next chunk starts with %s", (_label, prev, next) => {
      expect(pickDeltaJoin(prev, next)).toBe("\n\n");
    });

    it("inserts `\\n\\n` when the previous chunk ends with a closing code fence", () => {
      // Regression for the original screenshot bug:
      //   "...```\nHello World\n```" + "Нужен пример..." must become
      //   "...```\n\nНужен пример..." so the closing fence stays on its own
      //   line and the code block actually closes.
      const prev = '```python\nprint("Hello World")\n```\n\nВывод:\n```\nHello World\n```';
      const next = "Нужен пример посложнее?";
      expect(pickDeltaJoin(prev, next)).toBe("\n\n");
    });

    it.each([
      // Heading at very end of prev (no trailing newline) — otherwise next
      // chunk's text would get parsed as part of the heading title.
      ["heading at tail (no newline)", "## Захват Дании", "Body text"],
      ["heading mid-prev, no newline after", "Intro paragraph.\n\n## Захват Дании", "Body text"],
      ["h1 marker", "# Section", "Body"],
      ["h6 marker", "###### Subsection", "Body"],
    ])("inserts `\\n\\n` when prev's last line is an ATX heading (%s)", (_label, prev, next) => {
      expect(pickDeltaJoin(prev, next)).toBe("\n\n");
    });

    it.each([
      // Reproductions from the real "Weserübung" transcript the user reported.
      ["mid-phrase split", "**Продолжительность:** несколько", "часов (самая короткая кампания)"],
      // The chunker cut INSIDE a numbered list item; the prev block as a
      // whole starts with `1. ` but the cut is in the middle of the content.
      // We must NOT insert `\n\n` here — that would break the list item into
      // a separate paragraph. Default to a space.
      [
        "mid-list-item split (prev starts with `N. `)",
        "1. **Стратегия** — контроль над проливами (Эресунн, Большой Бельт, Малый",
        "Бельт)",
      ],
      [
        "mid-list-item split (prev starts with `- `)",
        "- В 4:15 утра немецкие войска перешли",
        "датскую границу без объявления войны",
      ],
      ["sentence-leading next", "Дания оставалась под оккупацией до 5 мая 1945 года.", "Хочешь"],
      ["isolated word + continuation", "Хочешь", "подробнее про Норвегию"],
      // Trailing whitespace on prev / leading whitespace on next must be
      // tolerated — the chunker can leave stray newlines in either place.
      ["prev with trailing newline", "несколько \n", "часов"],
      ["next with leading newline", "несколько", "\nчасов"],
    ])("inserts a single space for %s", (_label, prev, next) => {
      expect(pickDeltaJoin(prev, next)).toBe(" ");
    });

    it("falls back to empty string when either side is whitespace-only", () => {
      expect(pickDeltaJoin("   ", "next")).toBe("");
      expect(pickDeltaJoin("prev", "   ")).toBe("");
    });
  });

  describe("deliver integration", () => {
    // Text parts are posted to ``/turn/{id}/part`` via send.ts's real postOpenclawWebhook
    // → global fetch (the partial send.js mock only patches inbound.ts's import), so we
    // assert the join behaviour against a fetch mock.
    const originalFetch = globalThis.fetch;
    let fetchMock: ReturnType<typeof vi.fn>;

    afterEach(() => {
      globalThis.fetch = originalFetch;
    });

    async function dispatchOnce(): Promise<(p: { text: string }) => Promise<void>> {
      fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 200 }));
      globalThis.fetch = fetchMock as unknown as typeof fetch;
      readBodyMock.mockResolvedValue({
        ok: true,
        value: { chat_id: "c1", agent_id: "a", user_id: "u", text: "go" },
      });
      const { api, registerHttpRoute } = buildApi();
      registerInboundRoute(api as import("openclaw/plugin-sdk/core").OpenClawPluginApi);
      const handler = getHandler(registerHttpRoute);
      const req = { headers: { authorization: "Bearer secret" } } as IncomingMessage;
      const res = { statusCode: 0, end: vi.fn() } as unknown as ServerResponse;
      await handler(req, res);
      await vi.waitFor(() => expect(dispatchMock).toHaveBeenCalled());
      return (dispatchMock.mock.calls.at(-1)![0] as {
        deliver: (p: { text: string }) => Promise<void>;
      }).deliver;
    }

    function deltaTexts(): string[] {
      return fetchMock.mock.calls
        .filter((c) => /\/internal\/openclaw\/turn\/[0-9a-f-]+\/part$/.test(String(c[0])))
        .map((c) => JSON.parse(String((c[1] as RequestInit).body)) as { kind: string; text?: string })
        .filter((b) => b.kind === "text" && typeof b.text === "string")
        .map((b) => b.text as string)
    }

    it("sends the first delta verbatim", async () => {
      const deliver = await dispatchOnce();
      await deliver({ text: "Hello" });
      expect(deltaTexts()).toEqual(["Hello"]);
    });

    it("joins consecutive prose deltas with a single space, not a paragraph break", async () => {
      // Regression for "несколько / часов" style mid-phrase cuts.
      const deliver = await dispatchOnce();
      await deliver({ text: "**Продолжительность:** несколько" });
      await deliver({ text: "часов (самая короткая кампания)" });
      expect(deltaTexts().join("")).toBe(
        "**Продолжительность:** несколько часов (самая короткая кампания)",
      );
      expect(deltaTexts().join("")).not.toContain("несколько\n\nчасов");
    });

    it("escalates to `\\n\\n` when the next delta starts with a markdown structural marker", async () => {
      const deliver = await dispatchOnce();
      await deliver({ text: "Intro paragraph." });
      await deliver({ text: "## Section heading" });
      await deliver({ text: "Body text under heading." });
      await deliver({ text: "- bullet one\n- bullet two" });
      expect(deltaTexts().join("")).toBe(
        "Intro paragraph.\n\n## Section heading\n\nBody text under heading.\n\n- bullet one\n- bullet two",
      );
    });

    it("escalates to `\\n\\n` when the previous delta ends with a closing code fence", async () => {
      const deliver = await dispatchOnce();
      await deliver({
        text: '```python\nprint("Hello World")\n```',
      });
      await deliver({ text: "Нужен пример посложнее?" });
      const joined = deltaTexts().join("");
      expect(joined).toBe(
        '```python\nprint("Hello World")\n```\n\nНужен пример посложнее?',
      );
      expect(joined).not.toContain("``` Нужен");
    });

    it("ignores empty / whitespace-only deltas without consuming the first-chunk slot", async () => {
      const deliver = await dispatchOnce();
      await deliver({ text: "  " });
      await deliver({ text: "" });
      await deliver({ text: "First real chunk" });
      await deliver({ text: "More text" });
      expect(deltaTexts()).toEqual(["First real chunk", " More text"]);
    });

    it("drops runtime tool activity footers before posting text parts", async () => {
      const deliver = await dispatchOnce();
      await deliver({
        text: [
          "Сейчас посмотрю ваши заказы.",
          "🤖 Subagents",
          "🧾 Session History: session agent:marketing:subagent:87abb016-1111-2222-3333-444444444444, limit 20",
          "🛠️ sellerclaw agent-orders list failed",
          "Вот что удалось найти.",
        ].join("\n"),
      });
      expect(deltaTexts()).toEqual([
        "Сейчас посмотрю ваши заказы.\nВот что удалось найти.",
      ]);
    });

    it("does not post a text part when deliver payload is only runtime activity", async () => {
      const deliver = await dispatchOnce();
      await deliver({ text: "🤖 Subagents\n🛠️ sellerclaw agent-orders list failed" });
      expect(deltaTexts()).toEqual([]);
    });

    // Regression for the "Weserübung" transcript the user reported: many
    // mid-phrase deltas (e.g. `"...несколько"` + `"часов..."`) that the
    // previous "always-\n\n" implementation turned into spurious paragraph
    // breaks all over the message body.
    it("reassembles a mixed prose / list / heading transcript without spurious paragraph breaks", async () => {
      const deliver = await dispatchOnce();
      // Each `await deliver({ text: ... })` simulates one chunk emitted by
      // the OpenClaw block-streaming chunker.
      await deliver({
        text: "**Операция «Везерюбунг»** — кодовое название вторжения в Данию и Норвегию",
      });
      await deliver({ text: "9 апреля 1940 года." });
      await deliver({ text: "## Захват Дании" });
      await deliver({
        text: "**Дата:** 9 апреля 1940 года \n**Продолжительность:** несколько",
      });
      await deliver({ text: "часов (самая короткая кампания Второй мировой)" });
      await deliver({
        text: "### Как проходило:\n- В 4:15 утра немецкие войска перешли",
      });
      await deliver({ text: "датскую границу без объявления войны" });
      const joined = deltaTexts().join("");

      // Heading on its own line, with blank line both above and below.
      expect(joined).toContain("\n\n## Захват Дании\n\n");
      expect(joined).toContain("\n\n### Как проходило:\n");

      // Mid-phrase splits stay glued by a single space, NOT a paragraph break.
      expect(joined).toContain("несколько часов (самая короткая");
      expect(joined).not.toContain("несколько\n\nчасов");
      expect(joined).toContain("перешли датскую границу");
      expect(joined).not.toContain("перешли\n\nдатскую");
    });
  });

  describe("readDeliverPayload", () => {
    it("returns empty fields for non-object payloads", () => {
      expect(readDeliverPayload(null)).toEqual({ text: "", mediaUrls: [] });
      expect(readDeliverPayload("nope")).toEqual({ text: "", mediaUrls: [] });
    });

    it("merges mediaUrls and the legacy mediaUrl field, deduped and trimmed", () => {
      expect(
        readDeliverPayload({
          text: "caption",
          mediaUrls: [" /a.png ", "/b.png", "/a.png"],
          mediaUrl: "/b.png",
        }),
      ).toEqual({ text: "caption", mediaUrls: ["/a.png", "/b.png"] });
    });

    it("ignores non-string media entries", () => {
      expect(
        readDeliverPayload({ text: "", mediaUrls: [42, null, "/ok.png", ""] }),
      ).toEqual({ text: "", mediaUrls: ["/ok.png"] });
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
    expect(
      fetchMock.mock.calls.filter((c) => !String(c[0]).includes("/internal/openclaw/turn")).length,
    ).toBe(1);
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

  it("persists non-image file_url parts via saveMediaBuffer and injects MEDIA marker", async () => {
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
    saveMediaBufferMock.mockResolvedValueOnce({
      id: "saved-csv",
      path: "/home/node/.openclaw/media/inbound/report---saved-csv.csv",
      size: 0,
      contentType: "text/csv",
    });
    const { fetchMock } = setFetchResponses([
      { ok: true, body: new ArrayBuffer(8), contentType: "text/csv" },
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

    // Host rewritten from localhost:8000 to apiBaseUrl host.
    expect(
      fetchMock.mock.calls.filter((c) => !String(c[0]).includes("/internal/openclaw/turn")).length,
    ).toBe(1);
    const [fetchedUrl, fetchInit] = fetchMock.mock.calls[0] as [
      string,
      { headers: Record<string, string> },
    ];
    expect(fetchedUrl).toBe("https://api.example/agent/files/x/report.csv");
    expect(fetchInit.headers.Authorization).toBe("Bearer sca");

    expect(saveMediaBufferMock).toHaveBeenCalledTimes(1);
    const saveArgs = saveMediaBufferMock.mock.calls[0]!;
    expect(Buffer.isBuffer(saveArgs[0])).toBe(true);
    expect(saveArgs[1]).toBe("text/csv");
    expect(saveArgs[2]).toBe("inbound");
    expect(saveArgs[4]).toBe("report.csv");

    const d = dispatchMock.mock.calls[0]![0] as { rawBody: string };
    expect(d.rawBody).toBe(
      "Look\nreport.csv\n[Attachment: file_id=x local=/home/node/.openclaw/media/inbound/report---saved-csv.csv]",
    );
  });

  it("persists PDF file_url parts with application/pdf content type", async () => {
    readBodyMock.mockResolvedValue({
      ok: true,
      value: {
        chat_id: "c1",
        agent_id: "supervisor",
        user_id: "u1",
        text: "Summarize",
        raw_content: [
          { type: "text", text: "Summarize" },
          {
            type: "file_url",
            file_url: { url: "https://api.example/agent/files/y/whitepaper.pdf" },
            filename: "whitepaper.pdf",
            content_type: "application/pdf",
          },
        ],
      },
    });
    saveMediaBufferMock.mockResolvedValueOnce({
      id: "saved-pdf",
      path: "/home/node/.openclaw/media/inbound/whitepaper---saved-pdf.pdf",
      size: 0,
      contentType: "application/pdf",
    });
    setFetchResponses([{ ok: true, body: new ArrayBuffer(16), contentType: "application/pdf" }]);

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

    expect(saveMediaBufferMock).toHaveBeenCalledTimes(1);
    const saveArgs = saveMediaBufferMock.mock.calls[0]!;
    expect(saveArgs[1]).toBe("application/pdf");
    expect(saveArgs[4]).toBe("whitepaper.pdf");

    const d = dispatchMock.mock.calls[0]![0] as { rawBody: string };
    expect(d.rawBody).toBe(
      "Summarize\nwhitepaper.pdf\n[Attachment: file_id=y local=/home/node/.openclaw/media/inbound/whitepaper---saved-pdf.pdf]",
    );
  });

  it("falls back to [attachment unavailable] when non-image file fetch fails", async () => {
    readBodyMock.mockResolvedValue({
      ok: true,
      value: {
        chat_id: "c1",
        agent_id: "supervisor",
        user_id: "u1",
        text: "Read",
        raw_content: [
          { type: "text", text: "Read" },
          {
            type: "file_url",
            file_url: { url: "https://api.example/agent/files/z/report.csv" },
            filename: "report.csv",
            content_type: "text/csv",
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
    expect(d.rawBody).toBe("Read\n[attachment unavailable: report.csv]");
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
    // First save for the image (uses the beforeEach default mock); then a
    // distinct path for the JSON file.
    saveMediaBufferMock.mockResolvedValueOnce({
      id: "saved-id",
      path: "/home/node/.openclaw/media/inbound/saved-id.jpg",
      size: 0,
      contentType: "image/jpeg",
    });
    saveMediaBufferMock.mockResolvedValueOnce({
      id: "saved-json",
      path: "/home/node/.openclaw/media/inbound/data---saved-json.json",
      size: 0,
      contentType: "application/json",
    });
    setFetchResponses([
      { ok: true, body: new ArrayBuffer(4), contentType: "image/jpeg" },
      { ok: true, body: new ArrayBuffer(4), contentType: "application/json" },
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

    expect(saveMediaBufferMock).toHaveBeenCalledTimes(2);
    const d = dispatchMock.mock.calls[0]![0] as { rawBody: string };
    expect(d.rawBody).toBe(
      [
        "see both",
        "[Image: source: /home/node/.openclaw/media/inbound/saved-id.jpg]",
        "data.json",
        "[Attachment: file_id=f local=/home/node/.openclaw/media/inbound/data---saved-json.json]",
      ].join("\n"),
    );
  });
});
