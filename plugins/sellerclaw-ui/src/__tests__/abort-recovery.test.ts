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

import {
  __resetOutboundDeliveries,
  deliverTextToChat,
  resolveSellerclawUiAccount,
  sellerclawUiChannelPlugin,
} from "../channel.js";
import {
  asLiveChatPrompt,
  registerAbortRoute,
  registerInboundRoute,
  UNANSWERED_PROMPT,
} from "../inbound.js";
import { __resetOwnerTurns } from "../owner-turns.js";
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
  /** Prompts of the turns that asked the agent again for a reply the owner never got. */
  reaskPrompts: () => string[];
  /** What each opened turn told the cloud it answers. */
  turnPairings: () => Array<Record<string, unknown>>;
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
  payloadExtra: Record<string, unknown> = {},
): Promise<TurnResult> {
  const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 200 }));
  globalThis.fetch = fetchMock as unknown as typeof fetch;

  readBodyMock.mockResolvedValue({
    ok: true,
    value: { chat_id: CHAT_ID, agent_id: "supervisor", user_id: "u1", text: "hi", ...payloadExtra },
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
    reaskPrompts: () =>
      dispatchMock.mock.calls
        .map((c) => String((c[0] as { rawBody?: unknown }).rawBody ?? ""))
        .filter((body) => body.includes("without a reply reaching the owner")),
    turnPairings: () =>
      bodiesFor(/\/internal\/openclaw\/turn$/).map((b) =>
        Object.fromEntries(
          Object.entries(b).filter(([k]) => k === "reply_to_message_id" || k === "unprompted"),
        ),
      ),
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
    __resetOutboundDeliveries();
    __resetOwnerTurns();
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

  it("resumes a dropped connection even when no run outcome was recorded", async () => {
    const { api, routes } = buildHarness();
    registerRunOutcomeTracker(api);
    registerInboundRoute(api);

    // No verdict is the norm, not an edge case: on the deployed runtime the agent run loads its
    // own plugin set and our ``agent_end`` subscription never fires. The engine's wording is
    // then the only evidence, and "timed out" is the pipe breaking — worth one more try.
    const turn = await runTurn(api, routes, deliverTimeoutNotice);

    await vi.waitFor(() => expect(turn.endStatuses()[0]).toBe("completed"));
    await vi.waitFor(() => expect(turn.continuationPrompts()).toHaveLength(1));
  });

  it("asks once more, then surfaces a failure it cannot attribute to the connection", async () => {
    const { api, routes } = buildHarness();
    registerRunOutcomeTracker(api);
    registerInboundRoute(api);

    // The owner got nothing, so the message is asked again — but as a request for the reply, not
    // a resume of broken work, and only once: the same failure the second time is surfaced.
    const turn = await runTurn(
      api,
      routes,
      async (deliver) => {
        await deliver({ text: "Model refused the request.", isError: true }, { kind: "final" });
      },
      true,
    );

    await vi.waitFor(() => expect(turn.endStatuses()).toEqual(["failed"]));
    expect(turn.continuationPrompts()).toHaveLength(0);
    expect(turn.reaskPrompts()).toHaveLength(1);
  });

  it("surfaces an unrecognised failure at once when the owner already had something", async () => {
    const { api, routes } = buildHarness();
    registerRunOutcomeTracker(api);
    registerInboundRoute(api);

    const turn = await runTurn(api, routes, async (deliver) => {
      await deliver({ text: "Проверяю…" }, { kind: "final", assistantMessageIndex: 1 });
      await deliver({ text: "Model refused the request.", isError: true }, { kind: "final" });
    });

    await vi.waitFor(() => expect(turn.endStatuses()).toEqual(["failed"]));
    expect(turn.reaskPrompts()).toHaveLength(0);
    expect(turn.partTexts().join("")).toContain("Проверяю…");
  });

  it("resumes a provider connection that died mid-answer", async () => {
    const { api, routes, hooks } = buildHarness();
    registerRunOutcomeTracker(api);
    registerInboundRoute(api);

    const turn = await runTurn(api, routes, async (deliver) => {
      // The runtime does attribute this one — but it is still the connection, not the request.
      reportRunEnd(hooks, { success: false, error: "APIConnectionError" });
      await deliver(
        {
          text: "LLM request failed. rawError=litellm.APIConnectionError: Response payload is not completed",
          isError: true,
        },
        { kind: "final" },
      );
    });

    await vi.waitFor(() => expect(turn.endStatuses()[0]).toBe("completed"));
    await vi.waitFor(() => expect(turn.continuationPrompts()).toHaveLength(1));
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

    const turn = await runTurn(
      api,
      routes,
      async (deliver) => {
        // A subagent-completion run finishing at the same moment must not become this turn's
        // verdict — otherwise recovery would be decided by an unrelated run. The failure text is
        // deliberately not a transport one, so a resume here could only come from that verdict.
        // The owner still got no reply, which is asked for again on its own terms.
        reportRunEnd(hooks, { success: false, runId: "announce:v1:agent:sellercart:subagent:x" });
        await deliver({ text: "Model refused the request.", isError: true }, { kind: "final" });
      },
      true,
    );

    await vi.waitFor(() => expect(turn.endStatuses()).toEqual(["failed"]));
    expect(turn.continuationPrompts()).toHaveLength(0);
    expect(turn.reaskPrompts()).toHaveLength(1);
  });
});

describe("owner message left unanswered", () => {
  const originalFetch = globalThis.fetch;

  beforeEach(() => {
    vi.clearAllMocks();
    __resetRunOutcomeState();
    __resetOutboundDeliveries();
    __resetOwnerTurns();
    dispatchMock.mockResolvedValue(undefined);
    resolveSessionMock.mockReturnValue("session-id-1");
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  /** What the engine appends when a tool call failed and the answer beside it said nothing. */
  const YIELD_WARNING = "⚠️ sessions_yield failed: no child run to wait for in this turn";

  /** The agent answering through the ``message`` tool: its own outbound turn, not this dispatch. */
  const sendWithMessageTool = async (text: string): Promise<void> => {
    await deliverTextToChat(resolveSellerclawUiAccount(CONFIG as never), SESSION_KEY, text);
  };

  it.each([
    {
      name: "the run ended on the silent token",
      run: async (_deliver: DeliverFn, hooks: Map<string, HookHandler>) => {
        reportRunEnd(hooks, { success: true });
      },
    },
    {
      // The 2026-09-21 chat: "Report the status of the task" → a failed sessions_yield, then
      // NO_REPLY, because the agent believed it had already sent the status.
      name: "a failed yield left only the engine's warning",
      run: async (deliver: DeliverFn, hooks: Map<string, HookHandler>) => {
        reportRunEnd(hooks, { success: true });
        await deliver({ text: YIELD_WARNING, isError: true }, { kind: "final" });
      },
    },
    {
      name: "the same warning with no verdict recorded",
      run: async (deliver: DeliverFn) => {
        await deliver({ text: YIELD_WARNING, isError: true }, { kind: "final" });
      },
    },
  ])("asks the agent again when $name", async ({ run }) => {
    const { api, routes, hooks } = buildHarness();
    registerRunOutcomeTracker(api);
    registerInboundRoute(api);
    dispatchMock
      .mockImplementationOnce(async (arg: { deliver: DeliverFn }) => run(arg.deliver, hooks))
      .mockImplementationOnce(async (arg: { deliver: DeliverFn }) => {
        await arg.deliver({ text: "Задача ещё идёт: поставщики проверены на 60%." }, { kind: "final" });
      });

    const turn = await runTurn(api, routes, undefined, false, {
      text: "[request-effort: medium]\n\nReport the status of the task",
      effort: "medium",
    });

    // The silent turn is not closed — no error note, and the owner's message stays pending —
    // and the re-asked turn carries the reply.
    await vi.waitFor(() => expect(turn.endStatuses()).toEqual(["completed"]));
    expect(turn.partTexts()).toEqual(["Задача ещё идёт: поставщики проверены на 60%."]);
    expect(turn.partTexts().join("")).not.toContain("sessions_yield");
    // Marked like the owner's own messages, so the agent reads it as live chat that owes a reply.
    expect(turn.reaskPrompts()).toEqual([`[request-effort: medium]\n${UNANSWERED_PROMPT}`]);
    expect(turn.continuationPrompts()).toHaveLength(0);
  });

  it("keeps the re-asked reply and both runs' thoughts in one assistant message", async () => {
    const { api, routes, hooks } = buildHarness();
    registerRunOutcomeTracker(api);
    registerInboundRoute(api);
    type RunArg = {
      deliver: DeliverFn;
      replyOptions: {
        onReasoningStream: (evt: { text?: string }) => void;
        onReasoningEnd: () => void;
      };
    };
    const think = (arg: RunArg, text: string): void => {
      arg.replyOptions.onReasoningStream({ text });
      arg.replyOptions.onReasoningEnd();
    };
    dispatchMock
      .mockImplementationOnce(async (arg: RunArg) => {
        think(arg, "I already sent the status.");
        reportRunEnd(hooks, { success: true });
      })
      .mockImplementationOnce(async (arg: RunArg) => {
        think(arg, "Nothing reached them, answering now.");
        await arg.deliver({ text: "Still running." }, { kind: "final" });
      });

    const turn = await runTurn(api, routes);

    await vi.waitFor(() => expect(turn.endStatuses()).toEqual(["completed"]));
    const calls = (pattern: RegExp) =>
      turn.fetchMock.mock.calls
        .filter((c) => pattern.test(String(c[0])))
        .map((c) => ({
          url: String(c[0]),
          body: JSON.parse(String((c[1] as RequestInit).body)) as Record<string, unknown>,
        }));
    await vi.waitFor(() => expect(calls(/\/internal\/openclaw\/thought$/)).toHaveLength(2));
    const thoughts = calls(/\/internal\/openclaw\/thought$/).map((c) => c.body);
    // Numbered on from the first run: the chat drops a thought whose number it already has.
    expect(thoughts.map((t) => [t.seq, t.text])).toEqual([
      [0, "I already sent the status."],
      [1, "Nothing reached them, answering now."],
    ]);
    const messageId = String(thoughts[0]!.message_id);
    expect(thoughts[1]!.message_id).toBe(messageId);
    expect(calls(/\/internal\/openclaw\/turn\/[0-9a-f-]+\/end$/).map((c) => c.url)).toEqual([
      expect.stringContaining(`/turn/${messageId}/end`),
    ]);
  });

  it.each([
    {
      // "Don't reply to this" — the agent stays silent, is told nothing arrived, and stays silent
      // again. That is its answer, not a failure: no error note, and no third ask.
      name: "a run that chose silence twice",
      run: async (_deliver: DeliverFn, hooks: Map<string, HookHandler>) => {
        reportRunEnd(hooks, { success: true });
      },
    },
    {
      name: "a finished run whose only output twice was a tool-failure warning",
      run: async (deliver: DeliverFn, hooks: Map<string, HookHandler>) => {
        reportRunEnd(hooks, { success: true });
        await deliver({ text: YIELD_WARNING, isError: true }, { kind: "final" });
      },
    },
  ])("closes quietly after asking once, for $name", async ({ run }) => {
    const { api, routes, hooks } = buildHarness();
    registerRunOutcomeTracker(api);
    registerInboundRoute(api);
    dispatchMock.mockImplementation(async (arg: { deliver: DeliverFn }) => run(arg.deliver, hooks));

    const turn = await runTurn(api, routes);

    await vi.waitFor(() => expect(turn.endStatuses()).toEqual(["completed"]));
    expect(turn.reaskPrompts()).toHaveLength(1);
    expect(dispatchMock).toHaveBeenCalledTimes(2);
  });

  /** The owner message the turn answers, as the cloud sends it. */
  const QUESTION_ID = "0a2b4c6d-8e0f-4a1b-9c3d-5e7f9a1b3c5d";

  /** A send on the outbound road with the reply target the engine put on it. */
  const sendReplyTo = async (text: string, replyToId: string): Promise<void> => {
    const plugin = sellerclawUiChannelPlugin as unknown as {
      outbound: { sendText: (p: unknown) => Promise<unknown> };
    };
    await plugin.outbound.sendText({
      account: resolveSellerclawUiAccount(CONFIG as never),
      to: `sellerclaw-ui:direct:${CHAT_ID}`,
      text,
      replyToId,
    });
  };

  type QueuedDispatch = {
    replyOptions: {
      abortSignal: AbortSignal;
      turnAdoptionLifecycle: { abortSignal: AbortSignal; onSettled: () => void };
    };
  };

  /** The engine could not add the message to the run going and queued it to run after that one. */
  const queueBehindActiveRun = (): Promise<QueuedDispatch> =>
    new Promise((resolve) => {
      dispatchMock.mockImplementationOnce(async (arg: QueuedDispatch) => {
        resolve(arg);
        return {
          dispatched: true,
          dispatchResult: { deferredToActiveRun: "followup", queuedFinal: false, counts: {} },
        };
      });
    });

  const turnEnds = (): number =>
    (globalThis.fetch as unknown as ReturnType<typeof vi.fn>).mock.calls.filter((c) =>
      /\/turn\/[0-9a-f-]+\/end$/.test(String(c[0])),
    ).length;

  it("closes at once a message the engine added to the running turn", async () => {
    const { api, routes } = buildHarness();
    registerRunOutcomeTracker(api);
    registerInboundRoute(api);
    // Added to the run going: the dispatch returns with nothing of its own, and that run's turn
    // delivers the answer.
    dispatchMock.mockResolvedValueOnce({
      dispatched: true,
      dispatchResult: { deferredToActiveRun: "steer", queuedFinal: false, counts: {} },
    });

    const turn = await runTurn(api, routes, undefined, false, { message_id: QUESTION_ID });

    expect(turn.endStatuses()).toEqual(["completed"]);
    expect(turn.reaskPrompts()).toHaveLength(0);
    expect(dispatchMock).toHaveBeenCalledTimes(1);
    // The empty turn closes this message by name — not whichever of the chat's is newest.
    expect(turn.turnPairings()).toEqual([{ reply_to_message_id: QUESTION_ID }]);
  });

  it("keeps a queued message pending until its run is over, and counts the answer routed to it", async () => {
    const { api, routes } = buildHarness();
    registerRunOutcomeTracker(api);
    registerInboundRoute(api);
    const queued = queueBehindActiveRun();

    const turnDone = runTurn(api, routes, undefined, false, { message_id: QUESTION_ID });
    const engine = await queued;
    await new Promise((resolve) => setTimeout(resolve, 20));
    // Waiting behind the run going: the owner's message stays pending, nothing is closed.
    expect(turnEnds()).toBe(0);
    await sendReplyTo("Amazon fits pet supplies best.", QUESTION_ID);
    engine.replyOptions.turnAdoptionLifecycle.onSettled();
    const turn = await turnDone;

    // The routed answer's own turn, then this turn's empty close — both naming the message.
    expect(turn.endStatuses()).toEqual(["completed", "completed"]);
    expect(turn.turnPairings()).toEqual([
      { reply_to_message_id: QUESTION_ID },
      { reply_to_message_id: QUESTION_ID },
    ]);
    expect(turn.reaskPrompts()).toHaveLength(0);
  });

  it("asks again when a queued message's run answered nothing", async () => {
    const { api, routes } = buildHarness();
    registerRunOutcomeTracker(api);
    registerInboundRoute(api);
    const queued = queueBehindActiveRun();
    dispatchMock.mockImplementationOnce(answerRun);

    const turnDone = runTurn(api, routes, undefined, false, { message_id: QUESTION_ID });
    (await queued).replyOptions.turnAdoptionLifecycle.onSettled();
    const turn = await turnDone;

    expect(turn.reaskPrompts()).toHaveLength(1);
    expect(turn.partTexts()).toEqual(["Here is the status."]);
    expect(turn.endStatuses()).toEqual(["completed"]);
  });

  it("drops a queued message on the owner's stop without asking again", async () => {
    const { api, routes } = buildHarness();
    registerRunOutcomeTracker(api);
    registerInboundRoute(api);
    registerAbortRoute(api);
    const queued = queueBehindActiveRun();

    const turnDone = runTurn(api, routes, undefined, false, { message_id: QUESTION_ID });
    const engine = await queued;
    readBodyMock.mockResolvedValue({ ok: true, value: { chat_id: CHAT_ID, agent_id: "supervisor" } });
    await handlerFor(routes, "/abort")({ headers: {} } as IncomingMessage, {
      statusCode: 0,
      end: vi.fn(),
    } as unknown as ServerResponse);
    // The engine never settles it here: the stop alone ends the wait.
    const turn = await turnDone;

    // Handed to the engine, which cancels the message in its queue by it.
    expect(engine.replyOptions.turnAdoptionLifecycle.abortSignal.aborted).toBe(true);
    expect(engine.replyOptions.abortSignal.aborted).toBe(true);
    expect(turn.endStatuses()).toEqual(["completed"]);
    expect(turn.reaskPrompts()).toHaveLength(0);
    expect(dispatchMock).toHaveBeenCalledTimes(1);
  });

  it("does not count a reply to another of the chat's messages as this one's answer", async () => {
    const { api, routes, hooks } = buildHarness();
    registerRunOutcomeTracker(api);
    registerInboundRoute(api);
    dispatchMock
      .mockImplementationOnce(async () => {
        // Another turn of the chat — an earlier question's queued run — answers on the same road.
        await sendReplyTo("Your earlier export is ready.", "5d4c3b2a-1f0e-4d9c-8b7a-6f5e4d3c2b1a");
        reportRunEnd(hooks, { success: true });
      })
      .mockImplementationOnce(answerRun);

    const turn = await runTurn(api, routes, undefined, false, { message_id: QUESTION_ID });

    await vi.waitFor(() => expect(turn.reaskPrompts()).toHaveLength(1));
  });

  it("leaves a turn alone that answered with the message tool", async () => {
    const { api, routes, hooks } = buildHarness();
    registerRunOutcomeTracker(api);
    registerInboundRoute(api);

    const turn = await runTurn(api, routes, async () => {
      // Card sent, then the turn ends on the silent token — the documented way to avoid an echo.
      await sendWithMessageTool("Запустил проверку поставщиков.");
      reportRunEnd(hooks, { success: true });
    });

    await vi.waitFor(() => expect(turn.endStatuses()).toEqual(["completed", "completed"]));
    // One end for the ``message`` send's own turn, one for the dispatch — nothing re-asked.
    expect(turn.reaskPrompts()).toHaveLength(0);
    expect(dispatchMock).toHaveBeenCalledTimes(1);
  });

  it("keeps a message-tool answer completed when the engine appends a tool-failure warning", async () => {
    const { api, routes, hooks } = buildHarness();
    registerRunOutcomeTracker(api);
    registerInboundRoute(api);

    const turn = await runTurn(api, routes, async (deliver) => {
      await sendWithMessageTool("Статус: задача в работе.");
      reportRunEnd(hooks, { success: true });
      await deliver({ text: YIELD_WARNING, isError: true }, { kind: "final" });
    });

    // Without counting the ``message`` send, this dispatch delivered nothing but the warning and
    // was closed as failed — an error note under an answer the owner had already read.
    await vi.waitFor(() => expect(turn.endStatuses()).toEqual(["completed", "completed"]));
    expect(turn.reaskPrompts()).toHaveLength(0);
  });

  it("does not count a message sent before this turn started", async () => {
    const { api, routes, hooks } = buildHarness();
    registerRunOutcomeTracker(api);
    registerInboundRoute(api);
    globalThis.fetch = vi
      .fn()
      .mockResolvedValue(new Response(null, { status: 200 })) as unknown as typeof fetch;
    await sendWithMessageTool("Earlier update.");
    await new Promise((resolve) => setTimeout(resolve, 5));
    dispatchMock
      .mockImplementationOnce(async () => {
        reportRunEnd(hooks, { success: true });
      })
      .mockImplementationOnce(answerRun);

    const turn = await runTurn(api, routes);

    await vi.waitFor(() => expect(turn.endStatuses()).toEqual(["completed"]));
    expect(turn.reaskPrompts()).toHaveLength(1);
  });

  it("does not ask again after the owner pressed stop", async () => {
    const { api, routes, hooks } = buildHarness();
    registerRunOutcomeTracker(api);
    registerInboundRoute(api);
    registerAbortRoute(api);

    readBodyMock.mockResolvedValue({ ok: true, value: { chat_id: CHAT_ID, agent_id: "supervisor" } });
    await handlerFor(routes, "/abort")({ headers: {} } as IncomingMessage, {
      statusCode: 0,
      end: vi.fn(),
    } as unknown as ServerResponse);

    const turn = await runTurn(api, routes, async () => {
      reportRunEnd(hooks, { success: false });
    });

    await vi.waitFor(() => expect(turn.endStatuses()).toEqual(["completed"]));
    expect(turn.reaskPrompts()).toHaveLength(0);
    expect(dispatchMock).toHaveBeenCalledTimes(1);
  });

  it("surfaces a failure only a person can fix without asking again", async () => {
    const { api, routes, hooks } = buildHarness();
    registerRunOutcomeTracker(api);
    registerInboundRoute(api);

    const turn = await runTurn(api, routes, async (deliver) => {
      reportRunEnd(hooks, { success: true });
      await deliver({ text: "Insufficient credits for this request.", isError: true }, { kind: "final" });
    });

    await vi.waitFor(() => expect(turn.endStatuses()).toEqual(["failed"]));
    expect(turn.reaskPrompts()).toHaveLength(0);
  });
});

/** A re-asked run that answers. */
async function answerRun(arg: { deliver: DeliverFn }): Promise<void> {
  await arg.deliver({ text: "Here is the status." }, { kind: "final" });
}

describe("asLiveChatPrompt", () => {
  it.each([
    { name: "puts the owner's effort line on top", effort: "high", expected: "[request-effort: high]\nP" },
    { name: "leaves the prompt as it is when no effort is known", effort: null, expected: "P" },
  ])("$name", ({ effort, expected }) => {
    expect(asLiveChatPrompt("P", effort)).toBe(expected);
  });
});
