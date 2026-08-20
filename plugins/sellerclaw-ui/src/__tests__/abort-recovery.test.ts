import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { IncomingMessage, ServerResponse } from "node:http";
import type { OpenClawPluginApi } from "openclaw/plugin-sdk/core";

const { dispatchMock, readBodyMock, abortRunMock, resolveSessionMock } = vi.hoisted(() => ({
  dispatchMock: vi.fn().mockResolvedValue(undefined),
  readBodyMock: vi.fn(),
  abortRunMock: vi.fn(),
  resolveSessionMock: vi.fn(),
}));

vi.mock("../inbound-reply-with-reasoning.js", () => ({
  dispatchInboundDirectDmWithReasoning: (...args: unknown[]) => dispatchMock(...args),
}));

vi.mock("openclaw/plugin-sdk/channel-inbound", () => ({
  dispatchInboundDirectDmWithRuntime: vi.fn(),
  runPreparedInboundReply: vi.fn(),
}));

vi.mock("openclaw/plugin-sdk/reply-payload", () => ({
  isReasoningReplyPayload: (payload: Record<string, unknown>) => payload?.isReasoning === true,
}));

vi.mock("openclaw/plugin-sdk/media-store", () => ({
  saveMediaBuffer: vi.fn(),
  resolveMediaBufferPath: vi.fn(),
}));

vi.mock("openclaw/plugin-sdk/webhook-ingress", () => ({
  readJsonWebhookBodyOrReject: readBodyMock,
}));

vi.mock("openclaw/plugin-sdk/agent-harness-runtime", () => ({
  abortAgentHarnessRun: (...args: unknown[]) => abortRunMock(...args),
  resolveActiveEmbeddedRunSessionId: (...args: unknown[]) => resolveSessionMock(...args),
}));

vi.mock("../runtime-store.js", () => ({
  getRuntime: () => ({}),
}));

import { registerAbortRoute, registerInboundRoute } from "../inbound.js";
import { __resetRunOutcomeState, registerRunOutcomeTracker } from "../run-outcome.js";

/** The chat from the 2026-08-19 staging incident; the session-key matcher needs a real uuid. */
const CHAT_ID = "b76fd17a-dfc2-49cd-94cf-1d1b3ffc889b";
const SESSION_KEY = `agent:supervisor:sellerclaw-ui:direct:${CHAT_ID}`;
const TIMEOUT_NOTICE = "LLM request timed out.";

const CONFIG = {
  channels: {
    "sellerclaw-ui": {
      apiBaseUrl: "https://api.example",
      userId: "user-1",
      agentApiKey: "sca",
      internalWebhookSecret: "secret",
    },
  },
} as Record<string, unknown>;

type HookHandler = (event: unknown, ctx?: unknown) => unknown;
type DeliverFn = (payload: Record<string, unknown>, info?: Record<string, unknown>) => Promise<void>;

function buildHarness() {
  const routes: Array<{ path: string; handler: unknown }> = [];
  const hooks = new Map<string, HookHandler>();
  const api = {
    config: CONFIG,
    logger: { info: vi.fn(), warn: vi.fn(), error: vi.fn() },
    registerHttpRoute: (opts: { path: string; handler: unknown }) => routes.push(opts),
    on: (event: string, handler: HookHandler) => hooks.set(event, handler),
  } as unknown as OpenClawPluginApi;
  return { api, routes, hooks };
}

function handlerFor(
  routes: Array<{ path: string; handler: unknown }>,
  suffix: string,
): (req: IncomingMessage, res: ServerResponse) => Promise<boolean> {
  const route = routes.find((r) => r.path.endsWith(suffix));
  if (!route) throw new Error(`no route ending in ${suffix}`);
  return route.handler as (req: IncomingMessage, res: ServerResponse) => Promise<boolean>;
}

/**
 * Report how a run ended, the way the runtime's ``agent_end`` hook does.
 *
 * On the deployed runtime an abort suppresses ``error`` on purpose, so "no error" is the
 * budget-timeout family and a set ``error`` is a provider failure that needs a human.
 */
function reportRunEnd(
  hooks: Map<string, HookHandler>,
  outcome: { success: boolean; error?: string; runId?: string },
): void {
  const handler = hooks.get("agent_end");
  if (!handler) throw new Error("agent_end hook not registered");
  handler(
    { runId: outcome.runId ?? "run-1", success: outcome.success, error: outcome.error },
    { sessionKey: SESSION_KEY },
  );
}

interface TurnResult {
  fetchMock: ReturnType<typeof vi.fn>;
  endStatuses: () => string[];
  partTexts: () => string[];
  continuationPrompts: () => string[];
}

/**
 * Drive one inbound turn. ``onDispatch`` runs inside the dispatch, so anything it delivers is
 * ordered before ``finishTurn`` — exactly as the engine orders a real run.
 *
 * ``persist`` applies the same behaviour to every dispatch of this turn, including the
 * continuations it spawns; without it only the first dispatch is scripted and a continuation
 * resolves as an ordinary empty turn.
 */
async function runTurn(
  api: OpenClawPluginApi,
  routes: Array<{ path: string; handler: unknown }>,
  onDispatch?: (deliver: DeliverFn) => Promise<void>,
  persist = false,
): Promise<TurnResult> {
  const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 200 }));
  globalThis.fetch = fetchMock as unknown as typeof fetch;

  readBodyMock.mockResolvedValue({
    ok: true,
    value: { chat_id: CHAT_ID, agent_id: "supervisor", user_id: "u1", text: "hi" },
  });
  if (onDispatch) {
    const impl = async (arg: { deliver: DeliverFn }) => {
      await onDispatch(arg.deliver);
    };
    if (persist) dispatchMock.mockImplementation(impl);
    else dispatchMock.mockImplementationOnce(impl);
  }

  const handler = handlerFor(routes, "/inbound");
  await handler({ headers: {} } as IncomingMessage, {
    statusCode: 0,
    end: vi.fn(),
  } as unknown as ServerResponse);

  const bodiesFor = (suffix: RegExp): Array<Record<string, unknown>> =>
    fetchMock.mock.calls
      .filter((c) => suffix.test(String(c[0])))
      .map((c) => JSON.parse(String((c[1] as RequestInit).body)) as Record<string, unknown>);

  return {
    fetchMock,
    endStatuses: () =>
      bodiesFor(/\/internal\/openclaw\/turn\/[0-9a-f-]+\/end$/).map((b) => String(b.status)),
    partTexts: () =>
      bodiesFor(/\/internal\/openclaw\/turn\/[0-9a-f-]+\/part$/)
        .filter((b) => b.kind === "text")
        .map((b) => String(b.text ?? "")),
    continuationPrompts: () =>
      dispatchMock.mock.calls
        .map((c) => String((c[0] as { rawBody?: unknown }).rawBody ?? ""))
        .filter((body) => body.includes("[internal] Your previous turn hit the per-turn time")),
  };
}

/** Deliver the engine's failure notice the way an aborted run does: as the final payload. */
const deliverTimeoutNotice = async (deliver: DeliverFn): Promise<void> => {
  await deliver({ text: TIMEOUT_NOTICE, isError: true }, { kind: "final" });
};

describe("aborted turn recovery", () => {
  const originalFetch = globalThis.fetch;

  beforeEach(() => {
    vi.clearAllMocks();
    __resetRunOutcomeState();
    dispatchMock.mockResolvedValue(undefined);
    resolveSessionMock.mockReturnValue("session-id-1");
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  it("never posts the engine's failure notice as a chat part", async () => {
    const { api, routes, hooks } = buildHarness();
    registerRunOutcomeTracker(api);
    registerInboundRoute(api);

    const turn = await runTurn(api, routes, async (deliver) => {
      reportRunEnd(hooks, { success: false });
      await deliverTimeoutNotice(deliver);
    });

    await vi.waitFor(() => expect(turn.endStatuses().length).toBeGreaterThan(0));
    expect(turn.partTexts().join("")).not.toContain(TIMEOUT_NOTICE);
  });

  it("keeps text streamed before the abort and resumes the turn itself", async () => {
    const { api, routes, hooks } = buildHarness();
    registerRunOutcomeTracker(api);
    registerInboundRoute(api);

    const turn = await runTurn(api, routes, async (deliver) => {
      await deliver({ text: "Переношу товары…" }, { kind: "final", assistantMessageIndex: 2 });
      reportRunEnd(hooks, { success: false });
      await deliverTimeoutNotice(deliver);
    });

    await vi.waitFor(() => expect(turn.endStatuses()[0]).toBe("completed"));
    // What the owner already saw survives; only the engine notice is withheld.
    expect(turn.partTexts().join("")).toContain("Переношу товары…");
    expect(turn.partTexts().join("")).not.toContain(TIMEOUT_NOTICE);
    await vi.waitFor(() => expect(turn.continuationPrompts()).toHaveLength(1));
  });

  it("stops resuming after the attempt bound and tells the owner instead", async () => {
    const { api, routes, hooks } = buildHarness();
    registerRunOutcomeTracker(api);
    registerInboundRoute(api);

    // Every run in the chain aborts, so each continuation lands right back here.
    const turn = await runTurn(
      api,
      routes,
      async (deliver) => {
        reportRunEnd(hooks, { success: false });
        await deliverTimeoutNotice(deliver);
      },
      true,
    );

    // Two quiet recoveries, then the failure is surfaced rather than retried forever.
    await vi.waitFor(() =>
      expect(turn.endStatuses()).toEqual(["completed", "completed", "failed"]),
    );
    expect(turn.continuationPrompts()).toHaveLength(2);
    expect(turn.partTexts().join("")).not.toContain(TIMEOUT_NOTICE);
  });

  it("surfaces a failure family that needs a human, without retrying it", async () => {
    const { api, routes, hooks } = buildHarness();
    registerRunOutcomeTracker(api);
    registerInboundRoute(api);

    const turn = await runTurn(api, routes, async (deliver) => {
      // Billing/auth/rate-limit arrive with the error set: a retry would only burn credits.
      reportRunEnd(hooks, { success: false, error: "insufficient balance" });
      await deliver({ text: "Not enough credits.", isError: true }, { kind: "final" });
    });

    await vi.waitFor(() => expect(turn.endStatuses()).toEqual(["failed"]));
    expect(turn.continuationPrompts()).toHaveLength(0);
  });

  it("surfaces the failure when no run outcome was recorded", async () => {
    const { api, routes } = buildHarness();
    registerRunOutcomeTracker(api);
    registerInboundRoute(api);

    // The hook is fire-and-forget upstream, so it may not have landed. Recovery must not be
    // guessed at: no verdict means no retry.
    const turn = await runTurn(api, routes, deliverTimeoutNotice);

    await vi.waitFor(() => expect(turn.endStatuses()).toEqual(["failed"]));
    expect(turn.continuationPrompts()).toHaveLength(0);
  });

  it("treats an owner stop as neither an error nor something to resume", async () => {
    const { api, routes, hooks } = buildHarness();
    registerRunOutcomeTracker(api);
    registerInboundRoute(api);
    registerAbortRoute(api);

    readBodyMock.mockResolvedValue({
      ok: true,
      value: { chat_id: CHAT_ID, agent_id: "supervisor" },
    });
    await handlerFor(routes, "/abort")({ headers: {} } as IncomingMessage, {
      statusCode: 0,
      end: vi.fn(),
    } as unknown as ServerResponse);
    expect(abortRunMock).toHaveBeenCalledWith("session-id-1");

    const turn = await runTurn(api, routes, async (deliver) => {
      // A stop unwinds into the same terminal state as a budget death.
      reportRunEnd(hooks, { success: false });
      await deliverTimeoutNotice(deliver);
    });

    await vi.waitFor(() => expect(turn.endStatuses()).toEqual(["completed"]));
    expect(turn.continuationPrompts()).toHaveLength(0);
    expect(turn.partTexts().join("")).not.toContain(TIMEOUT_NOTICE);
  });

  it("keeps a successful turn completed when the engine appends a tool-failure warning", async () => {
    const { api, routes, hooks } = buildHarness();
    registerRunOutcomeTracker(api);
    registerInboundRoute(api);

    const turn = await runTurn(api, routes, async (deliver) => {
      // A mid-turn tool failure makes the engine push an ``isError`` warning payload BESIDE
      // the real answer of a run that ends successfully. That must not read as a failed turn.
      await deliver({ text: "Вот ответ." }, { kind: "final", assistantMessageIndex: 2 });
      reportRunEnd(hooks, { success: true });
      await deliver({ text: "⚠️ web_fetch failed (503)", isError: true }, { kind: "final" });
    });

    await vi.waitFor(() => expect(turn.endStatuses()).toEqual(["completed"]));
    expect(turn.partTexts().join("")).toContain("Вот ответ.");
    expect(turn.partTexts().join("")).not.toContain("web_fetch failed");
    expect(turn.continuationPrompts()).toHaveLength(0);
  });

  it("surfaces a successful run whose only product was an error text", async () => {
    const { api, routes, hooks } = buildHarness();
    registerRunOutcomeTracker(api);
    registerInboundRoute(api);

    const turn = await runTurn(api, routes, async (deliver) => {
      // A mid-turn rate limit after tool calls ends the run without an abort ("success"),
      // with the injected error text as its whole output. Pretending success here would
      // leave a silent blank turn — the owner must see something happened.
      reportRunEnd(hooks, { success: true });
      await deliver({ text: "Rate limited, try again shortly.", isError: true }, { kind: "final" });
    });

    await vi.waitFor(() => expect(turn.endStatuses()).toEqual(["failed"]));
    expect(turn.continuationPrompts()).toHaveLength(0);
  });

  it("closes quietly when the owner's stop made the dispatch itself reject", async () => {
    const { api, routes } = buildHarness();
    registerRunOutcomeTracker(api);
    registerInboundRoute(api);
    registerAbortRoute(api);

    readBodyMock.mockResolvedValue({
      ok: true,
      value: { chat_id: CHAT_ID, agent_id: "supervisor" },
    });
    await handlerFor(routes, "/abort")({ headers: {} } as IncomingMessage, {
      statusCode: 0,
      end: vi.fn(),
    } as unknown as ServerResponse);

    // Some abort paths reject the dispatch instead of resolving it with an error final.
    const turn = await runTurn(api, routes, async () => {
      throw new Error("This operation was aborted");
    });

    await vi.waitFor(() => expect(turn.endStatuses()).toEqual(["completed"]));
    expect(turn.continuationPrompts()).toHaveLength(0);
  });

  it("leaves an ordinary turn alone", async () => {
    const { api, routes, hooks } = buildHarness();
    registerRunOutcomeTracker(api);
    registerInboundRoute(api);

    const turn = await runTurn(api, routes, async (deliver) => {
      await deliver({ text: "Готово!" }, { kind: "final", assistantMessageIndex: 2 });
      reportRunEnd(hooks, { success: true });
    });

    await vi.waitFor(() => expect(turn.endStatuses()).toEqual(["completed"]));
    expect(turn.partTexts().join("")).toContain("Готово!");
    expect(turn.continuationPrompts()).toHaveLength(0);
    expect(turn.fetchMock.mock.calls.map((c) => String(c[0])).some((u) => u.endsWith("/end"))).toBe(
      true,
    );
  });

  it("ignores an announce run's outcome, which shares the chat's session key", async () => {
    const { api, routes, hooks } = buildHarness();
    registerRunOutcomeTracker(api);
    registerInboundRoute(api);

    const turn = await runTurn(api, routes, async (deliver) => {
      // A subagent-completion run finishing at the same moment must not become this turn's
      // verdict — otherwise recovery would be decided by an unrelated run.
      reportRunEnd(hooks, { success: false, runId: "announce:v1:agent:sellercart:subagent:x" });
      await deliverTimeoutNotice(deliver);
    });

    await vi.waitFor(() => expect(turn.endStatuses()).toEqual(["failed"]));
    expect(turn.continuationPrompts()).toHaveLength(0);
  });
});
