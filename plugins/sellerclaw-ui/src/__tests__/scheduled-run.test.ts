import { beforeEach, describe, expect, it, vi } from "vitest";
import type { IncomingMessage, ServerResponse } from "node:http";

const { dispatchMock, readBodyMock, reportMock, turnStartMock, turnPartMock, turnEndMock } =
  vi.hoisted(() => ({
    dispatchMock: vi.fn().mockResolvedValue(undefined),
    readBodyMock: vi.fn(),
    reportMock: vi.fn().mockResolvedValue(undefined),
    turnStartMock: vi.fn().mockResolvedValue(undefined),
    turnPartMock: vi.fn().mockResolvedValue(undefined),
    turnEndMock: vi.fn().mockResolvedValue(undefined),
  }));

vi.mock("../inbound-reply-with-reasoning.js", () => ({
  dispatchInboundDirectDmWithReasoning: (...args: unknown[]) => dispatchMock(...args),
}));

vi.mock("openclaw/plugin-sdk/webhook-ingress", () => ({
  readJsonWebhookBodyOrReject: readBodyMock,
}));

vi.mock("../runtime-store.js", () => ({
  getRuntime: () => ({}),
}));

// Keep the real send.ts, but spy on the cloud report and the chat-turn helpers so we can assert
// the scheduled run reports its outcome and never streams a chat turn.
vi.mock("../send.js", async () => {
  const actual = await vi.importActual<typeof import("../send.js")>("../send.js");
  return {
    ...actual,
    postScheduledTaskRun: (...args: unknown[]) => reportMock(...args),
    postTurnStart: (...args: unknown[]) => turnStartMock(...args),
    postTurnPart: (...args: unknown[]) => turnPartMock(...args),
    postTurnEnd: (...args: unknown[]) => turnEndMock(...args),
  };
});

import { registerScheduledRunRoute } from "../inbound.js";

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

type HandlerFn = (req: IncomingMessage, res: ServerResponse) => Promise<boolean>;

function buildHandler(): { handler: HandlerFn; registerHttpRoute: ReturnType<typeof vi.fn> } {
  const registerHttpRoute = vi.fn();
  const api = {
    config: DEFAULT_CONFIG,
    logger: { info: vi.fn(), warn: vi.fn(), error: vi.fn() },
    registerHttpRoute,
  };
  registerScheduledRunRoute(api as import("openclaw/plugin-sdk/core").OpenClawPluginApi);
  return {
    handler: registerHttpRoute.mock.calls[0]![0].handler as HandlerFn,
    registerHttpRoute,
  };
}

const REQ = { headers: {} } as IncomingMessage;
function res(): ServerResponse {
  return { statusCode: 0, end: vi.fn() } as unknown as ServerResponse;
}

describe("registerScheduledRunRoute", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    dispatchMock.mockResolvedValue(undefined);
    reportMock.mockResolvedValue(undefined);
  });

  it("registers a gateway-authed /scheduled-run route", () => {
    const { registerHttpRoute } = buildHandler();
    const opts = registerHttpRoute.mock.calls[0]![0] as { path: string; auth: string };
    expect(opts.path).toBe("/api/channels/sellerclaw-ui/scheduled-run");
    expect(opts.auth).toBe("gateway");
  });

  it("returns 400 when run_id or instruction is missing", async () => {
    readBodyMock.mockResolvedValue({ ok: true, value: { run_id: "", instruction: "" } });
    const { handler } = buildHandler();
    const r = res();
    await handler(REQ, r);
    expect(r.statusCode).toBe(400);
    expect(dispatchMock).not.toHaveBeenCalled();
  });

  it("runs the instruction and reports OK with the agent's final reply as summary", async () => {
    readBodyMock.mockResolvedValue({
      ok: true,
      value: {
        run_id: "run-abc",
        agent_id: "supervisor",
        user_id: "u1",
        instruction: "Summarize yesterday's orders and email me.",
      },
    });
    dispatchMock.mockImplementation(
      async (params: { deliver: (p: unknown, d?: unknown) => Promise<void> }) => {
        await params.deliver(
          { text: "Emailed yesterday's order digest." },
          { kind: "final", assistantMessageIndex: 0 },
        );
      },
    );

    const { handler } = buildHandler();
    const r = res();
    await handler(REQ, r);

    // Accepts immediately, then runs asynchronously.
    expect(r.statusCode).toBe(202);
    expect(dispatchMock).toHaveBeenCalledTimes(1);
    // Isolated per-run session — not a chat.
    const params = dispatchMock.mock.calls[0]![0] as { peer: { id: string } };
    expect(params.peer.id).toBe("scheduled-task:run-abc");

    await vi.waitFor(() => expect(reportMock).toHaveBeenCalledTimes(1));
    expect(reportMock.mock.calls[0]![1]).toMatchObject({
      runId: "run-abc",
      status: "ok",
      summary: "Emailed yesterday's order digest.",
    });
    // A scheduled run never streams a chat turn.
    expect(turnStartMock).not.toHaveBeenCalled();
    expect(turnPartMock).not.toHaveBeenCalled();
    expect(turnEndMock).not.toHaveBeenCalled();
  });

  it("reports ERROR when the run fails", async () => {
    readBodyMock.mockResolvedValue({
      ok: true,
      value: { run_id: "run-err", agent_id: "supervisor", user_id: "u1", instruction: "do it" },
    });
    dispatchMock.mockRejectedValue(new Error("stock feed timed out"));

    const { handler } = buildHandler();
    await handler(REQ, res());

    await vi.waitFor(() => expect(reportMock).toHaveBeenCalledTimes(1));
    const outcome = reportMock.mock.calls[0]![1] as { runId: string; status: string; error: string };
    expect(outcome.runId).toBe("run-err");
    expect(outcome.status).toBe("error");
    expect(outcome.error).toContain("stock feed timed out");
  });

  it("ignores reasoning payloads in the summary", async () => {
    readBodyMock.mockResolvedValue({
      ok: true,
      value: { run_id: "run-r", agent_id: "supervisor", user_id: "u1", instruction: "do it" },
    });
    dispatchMock.mockImplementation(
      async (params: { deliver: (p: unknown, d?: unknown) => Promise<void> }) => {
        await params.deliver({ text: "thinking… let me check", isReasoning: true }, { kind: "block" });
        await params.deliver({ text: "All good." }, { kind: "final", assistantMessageIndex: 0 });
      },
    );

    const { handler } = buildHandler();
    await handler(REQ, res());

    await vi.waitFor(() => expect(reportMock).toHaveBeenCalledTimes(1));
    expect(reportMock.mock.calls[0]![1]).toMatchObject({ status: "ok", summary: "All good." });
  });
});

describe("postScheduledTaskRun", () => {
  it("POSTs camelCase outcome to the cloud run webhook with the agent bearer", async () => {
    const { postScheduledTaskRun } = await vi.importActual<typeof import("../send.js")>("../send.js");
    const originalFetch = globalThis.fetch;
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    globalThis.fetch = fetchMock as unknown as typeof fetch;
    try {
      await postScheduledTaskRun(
        {
          apiBaseUrl: "https://api.example",
          userId: "user-1",
          agentApiKey: "sca-token",
          internalWebhookSecret: "secret",
          localAgentBaseUrl: "http://127.0.0.1:8001",
        },
        { runId: "run-1", status: "ok", summary: "done" },
      );
      expect(fetchMock).toHaveBeenCalledTimes(1);
      const [url, init] = fetchMock.mock.calls[0]! as [string, RequestInit];
      expect(url).toBe("https://api.example/agent/scheduled-tasks/run");
      expect((init.headers as Record<string, string>).Authorization).toBe("Bearer sca-token");
      expect(JSON.parse(String(init.body))).toEqual({
        runId: "run-1",
        status: "ok",
        summary: "done",
      });
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
});
