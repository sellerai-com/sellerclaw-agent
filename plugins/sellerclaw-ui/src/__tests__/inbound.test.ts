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

import { pickDeltaJoin, registerInboundRoute } from "../inbound.js";

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
    async function dispatchOnce(): Promise<(p: { text: string }) => Promise<void>> {
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
      return postWebhookMock.mock.calls
        .filter((c) => String(c[0]).includes("/internal/openclaw/stream-delta"))
        .map((c) => JSON.parse(String((c[1] as RequestInit).body)) as { text: string })
        .map((b) => b.text);
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
