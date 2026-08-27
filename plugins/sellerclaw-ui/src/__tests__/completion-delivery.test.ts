import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { OpenClawPluginApi } from "openclaw/plugin-sdk/core";

const deliverTextToChat = vi.fn().mockResolvedValue({ messageId: "delivered" });

vi.mock("../channel.js", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../channel.js")>();
  return {
    ...actual,
    deliverTextToChat,
    // The rescue path resolves an account from config; tests supply a bare one.
    resolveSellerclawUiAccount: () => ({
      apiBaseUrl: "http://api.test",
      userId: "user",
      agentApiKey: "key",
      internalWebhookSecret: "secret",
      localAgentBaseUrl: "http://127.0.0.1:8001",
    }),
  };
});

const { __resetCompletionDeliveryState, registerCompletionDeliveryGuard } = await import(
  "../completion-delivery.js"
);

const CHAT_ID = "48729abf-b759-46bf-8802-6768891edc7e";
const SESSION_KEY = `agent:supervisor:sellerclaw-ui:direct:${CHAT_ID}`;
const OTHER_SESSION_KEY = `agent:supervisor:sellerclaw-ui:direct:11111111-2222-3333-4444-555555555555`;
const ANNOUNCE_RUN_ID =
  "announce:v1:agent:ebay:subagent:cd0df525-f55c-45a9-b328-02baaa8ece25:a5aada61";
const CHILD_REPORT = "- **status**: success\n- **task**: dfcff8eb-1f2e-4a1b-9c3d-000000000000";
const ANSWER = "Листинг снят с продажи, деньги за рекламу больше не списываются.";

type HookHandler = (event: Record<string, unknown>, ctx?: Record<string, unknown>) => unknown;

function buildApi(): { api: OpenClawPluginApi; hooks: Map<string, HookHandler[]> } {
  const hooks = new Map<string, HookHandler[]>();
  const api = {
    config: {},
    logger: { info: vi.fn(), warn: vi.fn(), error: vi.fn() },
    registerHttpRoute: vi.fn(),
    on: (name: string, handler: HookHandler) => {
      const list = hooks.get(name) ?? [];
      list.push(handler);
      hooks.set(name, list);
    },
  } as unknown as OpenClawPluginApi;
  return { api, hooks };
}

/** One registered plugin, with each hook exposed as a plain call. */
function setup() {
  const { api, hooks } = buildApi();
  registerCompletionDeliveryGuard(api);
  const fire = (name: string, event: Record<string, unknown>, ctx?: Record<string, unknown>) => {
    const handlers = hooks.get(name) ?? [];
    let last: unknown;
    for (const handler of handlers) last = handler(event, ctx);
    return last;
  };
  return {
    api,
    hooks,
    toolCall: (event: Record<string, unknown>, ctx?: Record<string, unknown>) =>
      fire("before_tool_call", event, ctx),
    /**
     * How a run reports its answer now: the visible text of its model calls (``llm_output``),
     * then the run end that decides what to do with it. ``sessionKey`` rides the hook context —
     * the only place either handler reads it.
     */
    answerRun: (
      params: { runId?: unknown; text?: string; messages?: unknown },
      ctx: Record<string, unknown> = { sessionKey: SESSION_KEY },
    ) => {
      if (params.text !== undefined) {
        fire("llm_output", { runId: params.runId, assistantTexts: [params.text] }, ctx);
      }
      fire("agent_end", { runId: params.runId, messages: params.messages }, ctx);
    },
    agentEnd: (event: Record<string, unknown>, ctx?: Record<string, unknown>) =>
      fire("agent_end", event, ctx),
    sending: (event: Record<string, unknown>, ctx?: Record<string, unknown>) =>
      fire("message_sending", event, ctx) as
        | { content?: string; cancel?: boolean; cancelReason?: string }
        | undefined,
  };
}

beforeEach(() => {
  vi.useFakeTimers();
  deliverTextToChat.mockClear();
  __resetCompletionDeliveryState();
});

afterEach(() => {
  __resetCompletionDeliveryState();
  vi.useRealTimers();
});

describe("completion delivery", () => {
  it("substitutes the supervisor's answer for the child's raw report", () => {
    const { answerRun, sending } = setup();

    answerRun({ runId: ANNOUNCE_RUN_ID, text: ANSWER });
    const decision = sending(
      { to: `sellerclaw-ui:direct:${CHAT_ID}`, content: CHILD_REPORT },
      { sessionKey: SESSION_KEY, channelId: "sellerclaw-ui" },
    );

    expect(decision).toEqual({ content: ANSWER });
  });

  it("delivers an answer whose llm_output lands after the run end", () => {
    // The real order in OpenClaw 2026.8: ``agent_end`` is dispatched before ``llm_output`` for the
    // final model call. A run that writes its whole answer in its last round therefore looks
    // answerless at run end and is complete a heartbeat later. This is the case that reached a
    // seller as an empty chat after a 30-minute publishing job.
    const { hooks, sending } = setup();
    const fire = (name: string, event: Record<string, unknown>, ctx?: Record<string, unknown>) => {
      for (const handler of hooks.get(name) ?? []) handler(event, ctx);
    };
    const ctx = { sessionKey: SESSION_KEY };

    fire("agent_end", { runId: ANNOUNCE_RUN_ID, messages: undefined }, ctx);
    fire("llm_output", { runId: ANNOUNCE_RUN_ID, assistantTexts: [ANSWER] }, ctx);

    const decision = sending(
      { to: `sellerclaw-ui:direct:${CHAT_ID}`, content: CHILD_REPORT },
      { sessionKey: SESSION_KEY, channelId: "sellerclaw-ui" },
    );

    expect(decision).toEqual({ content: ANSWER });
  });

  it("registers all four hooks it depends on", () => {
    const { hooks } = setup();

    // ``before_agent_finalize`` is deliberately absent: a handler for it makes the runtime hold
    // back the whole visible reply stream until the run ends (see ``registerAnswerCapture``).
    expect([...hooks.keys()].sort()).toEqual([
      "agent_end",
      "before_tool_call",
      "llm_output",
      "message_sending",
    ]);
  });

  it("leaves a live chat turn alone after a silent announce left its token behind", () => {
    // The staging incident (chat 4148ca42, 2026-08-27). A duplicate completion event woke the
    // supervisor, which declined to speak: ``agent_end`` ran first and emptied the capture map, the
    // run's final ``llm_output`` then refilled it with the token, and nothing cleared it again.
    // Twelve minutes later the owner asked an ordinary question — and the delivery adopted that
    // leftover as its "raced" answer, replacing the 492 characters the agent had just written with
    // the word NO_REPLY. The answer was not hidden but destroyed: the chat kept the token.
    const { hooks, sending } = setup();
    const fire = (name: string, event: Record<string, unknown>, ctx?: Record<string, unknown>) => {
      for (const handler of hooks.get(name) ?? []) handler(event, ctx);
    };
    const ctx = { sessionKey: SESSION_KEY };
    const liveAnswer = "Да — миска в каталоге: добавлена сегодня из CJ и опубликована в Shopify.";

    fire("agent_end", { runId: ANNOUNCE_RUN_ID, messages: undefined }, ctx);
    fire("llm_output", { runId: ANNOUNCE_RUN_ID, assistantTexts: ["NO_REPLY"] }, ctx);
    vi.advanceTimersByTime(12 * 60_000);

    expect(sending({ content: liveAnswer }, ctx)).toBeUndefined();
  });

  it("cancels the fallback when the raced answer is the silent token, rather than sending it", () => {
    // Genuinely this run's own text, so the rescue applies — but the token means "deliver nothing".
    // Substituting it would put an internal sentinel in front of the owner; the outcome the run
    // asked for is silence.
    const { hooks, sending } = setup();
    const fire = (name: string, event: Record<string, unknown>, ctx?: Record<string, unknown>) => {
      for (const handler of hooks.get(name) ?? []) handler(event, ctx);
    };
    const ctx = { sessionKey: SESSION_KEY };

    fire("agent_end", { runId: ANNOUNCE_RUN_ID, messages: undefined }, ctx);
    fire("llm_output", { runId: ANNOUNCE_RUN_ID, assistantTexts: ["NO_REPLY"] }, ctx);

    expect(sending({ content: CHILD_REPORT }, ctx)).toMatchObject({ cancel: true });
  });

  it("rescues a fresh raced answer even when the delivery names an unrelated run", () => {
    // ``message_sending`` is an outbound-path hook: nothing promises its context carries the id of
    // the run whose ``llm_output`` wrote the text. Rejecting on a mismatched id here would veto a
    // legitimate rescue and hand the owner an empty chat — the failure this module exists to
    // prevent — so the raced adopt discriminates by capture age alone.
    const { hooks, sending } = setup();
    const fire = (name: string, event: Record<string, unknown>, ctx?: Record<string, unknown>) => {
      for (const handler of hooks.get(name) ?? []) handler(event, ctx);
    };
    const ctx = { sessionKey: SESSION_KEY };

    fire("agent_end", { runId: ANNOUNCE_RUN_ID, messages: undefined }, ctx);
    fire("llm_output", { runId: ANNOUNCE_RUN_ID, assistantTexts: [ANSWER] }, ctx);

    expect(
      sending({ content: CHILD_REPORT }, { sessionKey: SESSION_KEY, runId: "delivery-run-777" }),
    ).toEqual({ content: ANSWER });
  });

  it("never answers a completion run with text an earlier run left behind", () => {
    // Same map, same chat, different task. The earlier run's report is not this one's answer, and
    // delivering it would tell the owner a job finished that this run knows nothing about.
    const { hooks, sending } = setup();
    const fire = (name: string, event: Record<string, unknown>, ctx?: Record<string, unknown>) => {
      for (const handler of hooks.get(name) ?? []) handler(event, ctx);
    };
    const ctx = { sessionKey: SESSION_KEY };
    const earlierRun =
      "announce:v1:agent:shopify:subagent:11111111-2222-3333-4444-555555555555:0000aaaa";

    fire("agent_end", { runId: earlierRun, messages: undefined }, ctx);
    fire("llm_output", { runId: earlierRun, assistantTexts: ["Публикация завершена."] }, ctx);
    vi.advanceTimersByTime(11_000);
    fire("agent_end", { runId: ANNOUNCE_RUN_ID, messages: undefined }, ctx);

    expect(sending({ content: CHILD_REPORT }, ctx)).toMatchObject({ cancel: true });
  });

  it("applies the answer only once, so a later delivery is untouched", () => {
    const { answerRun, sending } = setup();
    answerRun({ runId: ANNOUNCE_RUN_ID, text: ANSWER });

    expect(sending({ content: CHILD_REPORT }, { sessionKey: SESSION_KEY })).toEqual({
      content: ANSWER,
    });
    expect(sending({ content: "следующий ответ" }, { sessionKey: SESSION_KEY })).toBeUndefined();
  });

  it("leaves a run that sent the answer itself completely alone", () => {
    const { toolCall, answerRun, sending } = setup();

    toolCall(
      {
        toolName: "message",
        runId: ANNOUNCE_RUN_ID,
        params: { action: "send", target: `sellerclaw-ui:direct:${CHAT_ID}`, message: ANSWER },
      },
      { sessionKey: SESSION_KEY, runId: ANNOUNCE_RUN_ID },
    );
    answerRun({ runId: ANNOUNCE_RUN_ID, text: ANSWER });

    expect(sending({ content: ANSWER }, { sessionKey: SESSION_KEY })).toBeUndefined();
    vi.advanceTimersByTime(60_000);
    expect(deliverTextToChat).not.toHaveBeenCalled();
  });

  it("does not count a non-send message call as having answered the owner", () => {
    const { toolCall, answerRun, sending } = setup();

    toolCall(
      { toolName: "message", runId: ANNOUNCE_RUN_ID, params: { action: "list" } },
      { sessionKey: SESSION_KEY, runId: ANNOUNCE_RUN_ID },
    );
    answerRun({ runId: ANNOUNCE_RUN_ID, text: ANSWER });

    expect(sending({ content: CHILD_REPORT }, { sessionKey: SESSION_KEY })).toEqual({
      content: ANSWER,
    });
  });

  it("suppresses the raw report when the run produced no answer at all", () => {
    const { agentEnd, sending } = setup();

    agentEnd({ runId: ANNOUNCE_RUN_ID, messages: [], success: true }, { sessionKey: SESSION_KEY });
    const decision = sending({ content: CHILD_REPORT }, { sessionKey: SESSION_KEY });

    expect(decision).toMatchObject({ cancel: true });
    expect(decision?.cancelReason).toContain("no owner-facing answer");
  });

  it.each([
    ["NO_REPLY", "NO_REPLY"],
    ["lowercase no_reply", "no_reply"],
    ["whitespace only", "   "],
  ])("delivers nothing when the answer is silent (%s)", (_label, text) => {
    const { answerRun, sending } = setup();

    answerRun({ runId: ANNOUNCE_RUN_ID, text });

    expect(sending({ content: CHILD_REPORT }, { sessionKey: SESSION_KEY })).toMatchObject({
      cancel: true,
    });
  });

  it("passes media through untouched", () => {
    const { answerRun, sending } = setup();
    answerRun({ runId: ANNOUNCE_RUN_ID, text: ANSWER });

    // A media-only payload reaches the hook with empty text; the child's screenshot or export
    // is a deliverable, not its internal report.
    expect(sending({ content: "" }, { sessionKey: SESSION_KEY })).toBeUndefined();
    // …and the answer is still waiting for the text payload that follows.
    expect(sending({ content: CHILD_REPORT }, { sessionKey: SESSION_KEY })).toEqual({
      content: ANSWER,
    });
  });

  it("does not rewrite when the runtime already delivered the answer", () => {
    const { answerRun, sending } = setup();
    answerRun({ runId: ANNOUNCE_RUN_ID, text: ANSWER });

    expect(sending({ content: `${ANSWER}\n` }, { sessionKey: SESSION_KEY })).toBeUndefined();
  });

  it("sends the answer itself when the runtime never delivers, then swallows a late delivery", async () => {
    const { answerRun, sending } = setup();
    answerRun({ runId: ANNOUNCE_RUN_ID, text: ANSWER });

    await vi.advanceTimersByTimeAsync(5_000);

    expect(deliverTextToChat).toHaveBeenCalledTimes(1);
    expect(deliverTextToChat.mock.calls[0]?.[1]).toBe(SESSION_KEY);
    expect(deliverTextToChat.mock.calls[0]?.[2]).toBe(ANSWER);
    expect(sending({ content: CHILD_REPORT }, { sessionKey: SESSION_KEY })).toMatchObject({
      cancel: true,
    });
  });

  it("stops suppressing once the empty-answer window closes", () => {
    const { agentEnd, sending } = setup();
    agentEnd({ runId: ANNOUNCE_RUN_ID, messages: [], success: true }, { sessionKey: SESSION_KEY });

    vi.advanceTimersByTime(20_000);

    // Past the window a send in this chat is someone else's — cancelling it would swallow a
    // message the owner actually asked for.
    expect(sending({ content: "вот отчёт" }, { sessionKey: SESSION_KEY })).toBeUndefined();
  });

  it("forgets a captured answer once it goes stale", () => {
    const { answerRun, sending } = setup();
    answerRun({ runId: ANNOUNCE_RUN_ID, text: ANSWER });
    deliverTextToChat.mockClear();

    vi.advanceTimersByTime(120_000);

    expect(sending({ content: CHILD_REPORT }, { sessionKey: SESSION_KEY })).toBeUndefined();
  });

  it("keeps answers separate per chat", () => {
    const { answerRun, sending } = setup();
    answerRun({ runId: ANNOUNCE_RUN_ID, text: ANSWER });

    expect(sending({ content: CHILD_REPORT }, { sessionKey: OTHER_SESSION_KEY })).toBeUndefined();
    expect(sending({ content: CHILD_REPORT }, { sessionKey: SESSION_KEY })).toEqual({
      content: ANSWER,
    });
  });

  it("recognizes a completion run from the trigger text when the run id is not prefixed", () => {
    const { answerRun, sending } = setup();

    answerRun({
      runId: "run-42",
      text: ANSWER,
      messages: [
        { role: "user", content: "давай снимем этот листинг с продажи" },
        { role: "assistant", content: [{ type: "toolCall", name: "message" }] },
        {
          role: "user",
          content: [
            { type: "text", text: "A background task completed. Use this result to reply." },
          ],
        },
      ],
    });

    expect(sending({ content: CHILD_REPORT }, { sessionKey: SESSION_KEY })).toEqual({
      content: ANSWER,
    });
  });

  it("leaves a live chat turn alone even when an older completion trigger is in history", () => {
    const { answerRun, sending } = setup();

    answerRun({
      runId: "run-43",
      text: ANSWER,
      messages: [
        { role: "user", content: "A background task completed. Use this result to reply." },
        { role: "assistant", content: [{ type: "text", text: "Снял." }] },
        { role: "user", content: "ну что?" },
      ],
    });

    expect(sending({ content: "обычный ответ" }, { sessionKey: SESSION_KEY })).toBeUndefined();
  });

  it("ignores completions whose requester is not a sellerclaw-ui chat", () => {
    const subagentSession = "agent:ebay:subagent:cd0df525-f55c-45a9-b328-02baaa8ece25";
    const { answerRun, sending } = setup();

    answerRun({ runId: ANNOUNCE_RUN_ID, text: ANSWER }, { sessionKey: subagentSession });

    expect(sending({ content: CHILD_REPORT }, { sessionKey: subagentSession })).toBeUndefined();
  });

  it("substitutes even when the fallback send beats the run's end", () => {
    const { hooks, sending } = setup();
    // Only the per-model-call capture ran; ``agent_end`` is fire-and-forget and has not landed.
    for (const handler of hooks.get("llm_output") ?? []) {
      handler({ runId: ANNOUNCE_RUN_ID, assistantTexts: [ANSWER] }, { sessionKey: SESSION_KEY });
    }

    expect(sending({ content: CHILD_REPORT }, { sessionKey: SESSION_KEY })).toEqual({
      content: ANSWER,
    });
  });

  it("leaves a normal chat turn's own delivery alone when its end has not landed", () => {
    const { hooks, sending } = setup();
    // A live chat turn (no announce run id): its reply is the owner's answer, not a fallback.
    for (const handler of hooks.get("llm_output") ?? []) {
      handler({ runId: "run-77", assistantTexts: ["обычный ответ"] }, { sessionKey: SESSION_KEY });
    }

    expect(sending({ content: "обычный ответ" }, { sessionKey: SESSION_KEY })).toBeUndefined();
  });

  it("does not substitute at delivery time for a run that messaged the owner itself", () => {
    const { hooks, toolCall, sending } = setup();
    toolCall(
      {
        toolName: "message",
        runId: ANNOUNCE_RUN_ID,
        params: { action: "send", target: `sellerclaw-ui:direct:${CHAT_ID}`, message: ANSWER },
      },
      { sessionKey: SESSION_KEY, runId: ANNOUNCE_RUN_ID },
    );
    for (const handler of hooks.get("llm_output") ?? []) {
      handler({ runId: ANNOUNCE_RUN_ID, assistantTexts: [ANSWER] }, { sessionKey: SESSION_KEY });
    }

    expect(sending({ content: ANSWER }, { sessionKey: SESSION_KEY })).toBeUndefined();
  });

  it("reads the session key from the hook context", () => {
    const { answerRun, sending } = setup();

    answerRun({ runId: ANNOUNCE_RUN_ID, text: ANSWER }, { sessionKey: SESSION_KEY });

    expect(sending({ content: CHILD_REPORT }, { sessionKey: SESSION_KEY })).toEqual({
      content: ANSWER,
    });
  });

  it("does nothing at all when the runtime exposes no hook registration", () => {
    const api = {
      config: {},
      logger: { info: vi.fn(), warn: vi.fn(), error: vi.fn() },
      registerHttpRoute: vi.fn(),
    } as unknown as OpenClawPluginApi;

    expect(() => registerCompletionDeliveryGuard(api)).not.toThrow();
  });
});
