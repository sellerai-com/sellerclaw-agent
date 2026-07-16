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

  it("registers HTTP route /api/channels/sellerclaw-ui/inbound with gateway auth", () => {
    // The /api/channels prefix + auth:"gateway" make OpenClaw authenticate the
    // request with the gateway token and grant operator.write to the agent run
    // (required for sessions_spawn); the handler itself does no auth.
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
      gatewayRuntimeScopeSurface?: string;
      handler: unknown;
    };
    expect(opts.path).toBe("/api/channels/sellerclaw-ui/inbound");
    expect(opts.auth).toBe("gateway");
    expect(opts.gatewayRuntimeScopeSurface).toBeUndefined();
    expect(typeof opts.handler).toBe("function");
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
      headers: {},
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

      const req = { headers: {} } as IncomingMessage;
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

      const req = { headers: {} } as IncomingMessage;
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

      const req = { headers: {} } as IncomingMessage;
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

      const req = { headers: {} } as IncomingMessage;
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

      const req = { headers: {} } as IncomingMessage;
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

      const req = { headers: {} } as IncomingMessage;
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
      const req = { headers: {} } as IncomingMessage;
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

    // A blank line inside a table does not separate rows — it ends the table, and every row
    // after it renders as literal `| … |` text. That is the report the user reported broken,
    // so a cut between two rows must produce exactly one newline.
    describe("cuts inside a table", () => {
      it.each([
        [
          "row after row",
          "| Item | Qty |\n| --- | --- |\n| Collar | 4 |",
          "| Harness | 1 |",
        ],
        ["delimiter after header", "| Item | Qty |", "| --- | --- |"],
        ["indented row", "  | Collar | 4 |", "| Harness | 1 |"],
      ])("joins with a single newline (%s)", (_label, prev, next) => {
        expect(pickDeltaJoin(prev, next)).toBe("\n");
      });

      it.each([
        ["prev kept its trailing newline", "| Collar | 4 |\n", "| Harness | 1 |"],
        ["next kept its leading newline", "| Collar | 4 |", "\n| Harness | 1 |"],
      ])("adds nothing when the boundary already has a newline (%s)", (_label, prev, next) => {
        // The joiner is prepended to the RAW delta, so a second newline here would end the
        // table just as surely as guessing one from scratch.
        expect(pickDeltaJoin(prev, next)).toBe("");
      });

      it("still opens a table after prose with a paragraph break", () => {
        expect(pickDeltaJoin("Товары, которые будут удалены:", "| Item | Qty |")).toBe("\n\n");
      });

      it("does not mistake prose containing a pipe for a table", () => {
        expect(pickDeltaJoin("Filter by A | B and", "| Item | Qty |")).toBe("\n\n");
      });
    });
  });

  describe("deliver integration", () => {
    // Text travels on two roads, and which one a delivery takes is the whole point of these
    // tests. Streamed blocks go to ``/turn/{id}/preview`` — shown live, then discarded. The
    // model's own final text goes to ``/turn/{id}/part`` and is what the chat keeps.
    //
    // Both are posted via send.ts's real postOpenclawWebhook → global fetch (the partial
    // send.js mock only patches inbound.ts's import), so we assert against a fetch mock.
    const originalFetch = globalThis.fetch;
    let fetchMock: ReturnType<typeof vi.fn>;

    afterEach(() => {
      globalThis.fetch = originalFetch;
    });

    type DeliverWithInfo = (
      p: Record<string, unknown>,
      info?: { kind?: string; assistantMessageIndex?: number },
    ) => Promise<void>;

    async function dispatchOnce(): Promise<DeliverWithInfo> {
      fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 200 }));
      globalThis.fetch = fetchMock as unknown as typeof fetch;
      readBodyMock.mockResolvedValue({
        ok: true,
        value: { chat_id: "c1", agent_id: "a", user_id: "u", text: "go" },
      });
      const { api, registerHttpRoute } = buildApi();
      registerInboundRoute(api as import("openclaw/plugin-sdk/core").OpenClawPluginApi);
      const handler = getHandler(registerHttpRoute);
      const req = { headers: {} } as IncomingMessage;
      const res = { statusCode: 0, end: vi.fn() } as unknown as ServerResponse;
      await handler(req, res);
      await vi.waitFor(() => expect(dispatchMock).toHaveBeenCalled());
      return (dispatchMock.mock.calls.at(-1)![0] as { deliver: DeliverWithInfo }).deliver;
    }

    function bodiesFor(pathSuffix: string): Record<string, unknown>[] {
      const re = new RegExp(`/internal/openclaw/turn/[0-9a-f-]+/${pathSuffix}$`);
      return fetchMock.mock.calls
        .filter((c) => re.test(String(c[0])))
        .map((c) => JSON.parse(String((c[1] as RequestInit).body)) as Record<string, unknown>);
    }

    /** Text committed to the chat message (``/part``, kind=text) — what the user keeps. */
    function deltaTexts(): string[] {
      return bodiesFor("part")
        .filter((b) => b.kind === "text" && typeof b.text === "string")
        .map((b) => b.text as string);
    }

    /** Text streamed as the throwaway live preview (``/preview``). */
    function previewTexts(): string[] {
      return bodiesFor("preview")
        .filter((b) => typeof b.text === "string")
        .map((b) => b.text as string);
    }

    /** One streamed block of a long reply. */
    const block = (index = 0) => ({ kind: "block", assistantMessageIndex: index });
    /** The model's own final text for a sub-message. */
    const final = (index = 0) => ({ kind: "final", assistantMessageIndex: index });

    it("sends the first preview delta verbatim", async () => {
      const deliver = await dispatchOnce();
      await deliver({ text: "Hello" }, block());
      expect(previewTexts()).toEqual(["Hello"]);
      expect(deltaTexts()).toEqual([]); // nothing committed until the model's final lands
    });

    it("joins consecutive prose previews with a single space, not a paragraph break", async () => {
      // Regression for "несколько / часов" style mid-phrase cuts.
      const deliver = await dispatchOnce();
      await deliver({ text: "**Продолжительность:** несколько" }, block());
      await deliver({ text: "часов (самая короткая кампания)" }, block());
      expect(previewTexts().join("")).toBe(
        "**Продолжительность:** несколько часов (самая короткая кампания)",
      );
      expect(previewTexts().join("")).not.toContain("несколько\n\nчасов");
    });

    it("escalates a preview to `\\n\\n` when the next delta starts with a markdown structural marker", async () => {
      const deliver = await dispatchOnce();
      await deliver({ text: "Intro paragraph." }, block());
      await deliver({ text: "## Section heading" }, block());
      await deliver({ text: "Body text under heading." }, block());
      await deliver({ text: "- bullet one\n- bullet two" }, block());
      expect(previewTexts().join("")).toBe(
        "Intro paragraph.\n\n## Section heading\n\nBody text under heading.\n\n- bullet one\n- bullet two",
      );
    });

    it("escalates a preview to `\\n\\n` when the previous delta ends with a closing code fence", async () => {
      const deliver = await dispatchOnce();
      await deliver({ text: '```python\nprint("Hello World")\n```' }, block());
      await deliver({ text: "Нужен пример посложнее?" }, block());
      const joined = previewTexts().join("");
      expect(joined).toBe(
        '```python\nprint("Hello World")\n```\n\nНужен пример посложнее?',
      );
      expect(joined).not.toContain("``` Нужен");
    });

    it("ignores empty / whitespace-only deltas without consuming the first-chunk slot", async () => {
      const deliver = await dispatchOnce();
      await deliver({ text: "  " }, block());
      await deliver({ text: "" }, block());
      await deliver({ text: "First real chunk" }, block());
      await deliver({ text: "More text" }, block());
      expect(previewTexts()).toEqual(["First real chunk", " More text"]);
    });

    // The runtime appends its tool/subagent footers into the same text chunks as real prose, so
    // any filter is a guess that also eats genuine lines (a bullet starting with 🛠️, a paragraph
    // headed "Reasoning:"). We forward the agent's text verbatim instead: losing the answer is
    // worse than showing a footer.
    it("forwards runtime tool activity lines verbatim instead of guessing what to strip", async () => {
      const deliver = await dispatchOnce();
      await deliver(
        {
          text: [
            "Сейчас посмотрю ваши заказы.",
            "🛠️ sellerclaw agent-orders list failed",
            "Вот что удалось найти.",
          ].join("\n"),
        },
        final(),
      );
      expect(deltaTexts()).toEqual([
        "Сейчас посмотрю ваши заказы.\n🛠️ sellerclaw agent-orders list failed\nВот что удалось найти.",
      ]);
    });

    // Regression for the "Weserübung" transcript the user reported: many
    // mid-phrase deltas (e.g. `"...несколько"` + `"часов..."`) that the
    // previous "always-\n\n" implementation turned into spurious paragraph
    // breaks all over the message body.
    it("reassembles a mixed prose / list / heading transcript without spurious paragraph breaks", async () => {
      const deliver = await dispatchOnce();
      // Each `await deliver({ text: ... }, block())` simulates one chunk emitted by
      // the OpenClaw block-streaming chunker.
      await deliver(
        { text: "**Операция «Везерюбунг»** — кодовое название вторжения в Данию и Норвегию" },
        block(),
      );
      await deliver({ text: "9 апреля 1940 года." }, block());
      await deliver({ text: "## Захват Дании" }, block());
      await deliver(
        { text: "**Дата:** 9 апреля 1940 года \n**Продолжительность:** несколько" },
        block(),
      );
      await deliver({ text: "часов (самая короткая кампания Второй мировой)" }, block());
      await deliver({ text: "### Как проходило:\n- В 4:15 утра немецкие войска перешли" }, block());
      await deliver({ text: "датскую границу без объявления войны" }, block());
      const joined = previewTexts().join("");

      // Heading on its own line, with blank line both above and below.
      expect(joined).toContain("\n\n## Захват Дании\n\n");
      expect(joined).toContain("\n\n### Как проходило:\n");

      // Mid-phrase splits stay glued by a single space, NOT a paragraph break.
      expect(joined).toContain("несколько часов (самая короткая");
      expect(joined).not.toContain("несколько\n\nчасов");
      expect(joined).toContain("перешли датскую границу");
      expect(joined).not.toContain("перешли\n\nдатскую");
    });

    // The bug this whole split exists for: the chunker cut a report inside a table, so the
    // next block began with `|` and the joiner guessed a paragraph break — ending the table
    // and leaving the remaining rows rendered as literal `| … |` text.
    it("keeps the table intact in both the preview and the committed final", async () => {
      const deliver = await dispatchOnce();
      await deliver({ text: "| Item | Qty |\n| --- | --- |\n| Collar | 4 |" }, block());
      await deliver({ text: "| Harness | 1 |" }, block());
      const finalText = "| Item | Qty |\n| --- | --- |\n| Collar | 4 |\n| Harness | 1 |";
      await deliver({ text: finalText }, final());

      // The preview reads the cut for what it is — a row boundary — so the table renders as
      // a table while it streams, rather than falling apart and snapping back at the end.
      expect(previewTexts().join("")).toBe(finalText);
      // And what the chat keeps is the model's own text regardless of how it was cut up.
      expect(deltaTexts()).toEqual([finalText]);
    });

    it("commits a final-only reply that was never streamed as blocks", async () => {
      const deliver = await dispatchOnce();
      await deliver({ text: "Quick answer." }, final());
      expect(deltaTexts()).toEqual(["Quick answer."]);
      expect(previewTexts()).toEqual([]);
    });

    it("treats a delivery with no dispatch info as a whole message", async () => {
      // Engines that don't block-stream (and older runtimes) deliver replies unlabelled;
      // an unlabelled delivery is a complete message, so it takes the committed road.
      const deliver = await dispatchOnce();
      await deliver({ text: "Here you go." });
      expect(deltaTexts()).toEqual(["Here you go."]);
      expect(previewTexts()).toEqual([]);
    });

    it("separates consecutive sub-messages of one dispatch with a blank line", async () => {
      const deliver = await dispatchOnce();
      // Sub-message 0: a short status (single block, then its final).
      await deliver({ text: "Working on it." }, block(0));
      await deliver({ text: "Working on it." }, final(0));
      // Sub-message 1: the answer (two blocks, then its final).
      await deliver({ text: "Answer one." }, block(1));
      await deliver({ text: "Answer two." }, block(1));
      await deliver({ text: "Answer one. Answer two." }, final(1));

      // Each sub-message is committed exactly once, as the model wrote it, and the two are
      // separated — the cloud concatenates these into one message body.
      expect(deltaTexts()).toEqual(["Working on it.", "\n\nAnswer one. Answer two."]);
    });

    it("starts a fresh preview after a final, so the next sub-message is not glued to the last", async () => {
      const deliver = await dispatchOnce();
      await deliver({ text: "Working on it." }, block(0));
      await deliver({ text: "Working on it." }, final(0));
      await deliver({ text: "Answer one." }, block(1));

      // The committed final ended sub-message 0 and dropped the preview with it; the next
      // block opens a new preview rather than continuing the old one.
      expect(previewTexts()).toEqual(["Working on it.", "Answer one."]);
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

    const req = { headers: {} } as IncomingMessage;
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

    const req = { headers: {} } as IncomingMessage;
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

    const req = { headers: {} } as IncomingMessage;
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

    const req = { headers: {} } as IncomingMessage;
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

    const req = { headers: {} } as IncomingMessage;
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

    const req = { headers: {} } as IncomingMessage;
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

describe("registerInboundRoute catch-up re-delivery", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    dispatchMock.mockResolvedValue(undefined);
    // finishTurn finalizes the turn via send.ts → global fetch (turn/start + turn/end).
    globalThis.fetch = vi
      .fn()
      .mockResolvedValue(new Response(null, { status: 200 })) as unknown as typeof fetch;
  });

  const body = (over: Record<string, unknown>) => ({
    ok: true,
    value: { chat_id: "c1", agent_id: "supervisor", user_id: "u1", text: "hi", ...over },
  });

  const makeReq = () => ({ headers: {} }) as IncomingMessage;

  it("drops a re-delivery while the original turn is still in flight", async () => {
    // Hang the first dispatch so the message stays in flight when the re-delivery lands.
    let release!: () => void;
    const gate = new Promise<void>((r) => (release = r));
    dispatchMock.mockReturnValueOnce(gate);

    readBodyMock.mockResolvedValueOnce(body({ message_id: "m1" }));
    readBodyMock.mockResolvedValueOnce(body({ message_id: "m1", redelivery: true }));

    const { api, registerHttpRoute } = buildApi();
    registerInboundRoute(api as import("openclaw/plugin-sdk/core").OpenClawPluginApi);
    const handler = getHandler(registerHttpRoute);

    const end1 = vi.fn();
    await handler(makeReq(), { statusCode: 0, end: end1 } as unknown as ServerResponse);
    await vi.waitFor(() => expect(dispatchMock).toHaveBeenCalledTimes(1));

    const end2 = vi.fn();
    const res2 = { statusCode: 0, end: end2 } as unknown as ServerResponse;
    await handler(makeReq(), res2);

    // The racing re-delivery is acknowledged (202) but NOT dispatched a second time.
    expect(res2.statusCode).toBe(202);
    expect(JSON.parse(end2.mock.calls[0]![0] as string)).toEqual({ ok: true, deduped: true });
    expect(dispatchMock).toHaveBeenCalledTimes(1);

    // Let the original turn finish so its dangling promises settle before the test ends.
    release();
    await vi.waitFor(() =>
      expect(globalThis.fetch as ReturnType<typeof vi.fn>).toHaveBeenCalled(),
    );
  });

  it("dispatches a re-delivery with a FRESH MessageSid (defeats session-level dedup)", async () => {
    readBodyMock.mockResolvedValueOnce(body({ message_id: "m2", redelivery: true }));

    const { api, registerHttpRoute } = buildApi();
    registerInboundRoute(api as import("openclaw/plugin-sdk/core").OpenClawPluginApi);
    const handler = getHandler(registerHttpRoute);

    await handler(makeReq(), { statusCode: 0, end: vi.fn() } as unknown as ServerResponse);
    await vi.waitFor(() => expect(dispatchMock).toHaveBeenCalledTimes(1));

    const sid = (dispatchMock.mock.calls[0]![0] as { messageId: string }).messageId;
    expect(sid).not.toBe("m2");
    expect(typeof sid).toBe("string");
    expect(sid.length).toBeGreaterThan(0);
  });

  it("dispatches a live message with its cloud message_id as MessageSid", async () => {
    readBodyMock.mockResolvedValueOnce(body({ message_id: "m3" }));

    const { api, registerHttpRoute } = buildApi();
    registerInboundRoute(api as import("openclaw/plugin-sdk/core").OpenClawPluginApi);
    const handler = getHandler(registerHttpRoute);

    await handler(makeReq(), { statusCode: 0, end: vi.fn() } as unknown as ServerResponse);
    await vi.waitFor(() => expect(dispatchMock).toHaveBeenCalledTimes(1));

    expect((dispatchMock.mock.calls[0]![0] as { messageId: string }).messageId).toBe("m3");
  });
});
