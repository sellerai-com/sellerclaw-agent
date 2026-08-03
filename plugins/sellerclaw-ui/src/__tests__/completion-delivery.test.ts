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
    finalize: (event: Record<string, unknown>, ctx?: Record<string, unknown>) =>
      fire("before_agent_finalize", event, ctx),
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
    const { finalize, sending } = setup();

    finalize({ runId: ANNOUNCE_RUN_ID, sessionKey: SESSION_KEY, lastAssistantMessage: ANSWER });
    const decision = sending(
      { to: `sellerclaw-ui:direct:${CHAT_ID}`, content: CHILD_REPORT },
      { sessionKey: SESSION_KEY, channelId: "sellerclaw-ui" },
    );

    expect(decision).toEqual({ content: ANSWER });
  });

  it("registers all four hooks it depends on", () => {
    const { hooks } = setup();

    expect([...hooks.keys()].sort()).toEqual([
      "agent_end",
      "before_agent_finalize",
      "before_tool_call",
      "message_sending",
    ]);
  });

  it("applies the answer only once, so a later delivery is untouched", () => {
    const { finalize, sending } = setup();
    finalize({ runId: ANNOUNCE_RUN_ID, sessionKey: SESSION_KEY, lastAssistantMessage: ANSWER });

    expect(sending({ content: CHILD_REPORT }, { sessionKey: SESSION_KEY })).toEqual({
      content: ANSWER,
    });
    expect(sending({ content: "следующий ответ" }, { sessionKey: SESSION_KEY })).toBeUndefined();
  });

  it("leaves a run that sent the answer itself completely alone", () => {
    const { toolCall, finalize, sending } = setup();

    toolCall(
      {
        toolName: "message",
        runId: ANNOUNCE_RUN_ID,
        params: { action: "send", target: `sellerclaw-ui:direct:${CHAT_ID}`, message: ANSWER },
      },
      { sessionKey: SESSION_KEY, runId: ANNOUNCE_RUN_ID },
    );
    finalize({ runId: ANNOUNCE_RUN_ID, sessionKey: SESSION_KEY, lastAssistantMessage: ANSWER });

    expect(sending({ content: ANSWER }, { sessionKey: SESSION_KEY })).toBeUndefined();
    vi.advanceTimersByTime(60_000);
    expect(deliverTextToChat).not.toHaveBeenCalled();
  });

  it("does not count a non-send message call as having answered the owner", () => {
    const { toolCall, finalize, sending } = setup();

    toolCall(
      { toolName: "message", runId: ANNOUNCE_RUN_ID, params: { action: "list" } },
      { sessionKey: SESSION_KEY, runId: ANNOUNCE_RUN_ID },
    );
    finalize({ runId: ANNOUNCE_RUN_ID, sessionKey: SESSION_KEY, lastAssistantMessage: ANSWER });

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
    const { finalize, sending } = setup();

    finalize({ runId: ANNOUNCE_RUN_ID, sessionKey: SESSION_KEY, lastAssistantMessage: text });

    expect(sending({ content: CHILD_REPORT }, { sessionKey: SESSION_KEY })).toMatchObject({
      cancel: true,
    });
  });

  it("passes media through untouched", () => {
    const { finalize, sending } = setup();
    finalize({ runId: ANNOUNCE_RUN_ID, sessionKey: SESSION_KEY, lastAssistantMessage: ANSWER });

    // A media-only payload reaches the hook with empty text; the child's screenshot or export
    // is a deliverable, not its internal report.
    expect(sending({ content: "" }, { sessionKey: SESSION_KEY })).toBeUndefined();
    // …and the answer is still waiting for the text payload that follows.
    expect(sending({ content: CHILD_REPORT }, { sessionKey: SESSION_KEY })).toEqual({
      content: ANSWER,
    });
  });

  it("does not rewrite when the runtime already delivered the answer", () => {
    const { finalize, sending } = setup();
    finalize({ runId: ANNOUNCE_RUN_ID, sessionKey: SESSION_KEY, lastAssistantMessage: ANSWER });

    expect(sending({ content: `${ANSWER}\n` }, { sessionKey: SESSION_KEY })).toBeUndefined();
  });

  it("sends the answer itself when the runtime never delivers, then swallows a late delivery", async () => {
    const { finalize, sending } = setup();
    finalize({ runId: ANNOUNCE_RUN_ID, sessionKey: SESSION_KEY, lastAssistantMessage: ANSWER });

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
    const { finalize, sending } = setup();
    finalize({ runId: ANNOUNCE_RUN_ID, sessionKey: SESSION_KEY, lastAssistantMessage: ANSWER });
    deliverTextToChat.mockClear();

    vi.advanceTimersByTime(120_000);

    expect(sending({ content: CHILD_REPORT }, { sessionKey: SESSION_KEY })).toBeUndefined();
  });

  it("keeps answers separate per chat", () => {
    const { finalize, sending } = setup();
    finalize({ runId: ANNOUNCE_RUN_ID, sessionKey: SESSION_KEY, lastAssistantMessage: ANSWER });

    expect(sending({ content: CHILD_REPORT }, { sessionKey: OTHER_SESSION_KEY })).toBeUndefined();
    expect(sending({ content: CHILD_REPORT }, { sessionKey: SESSION_KEY })).toEqual({
      content: ANSWER,
    });
  });

  it("recognizes a completion run from the trigger text when the run id is not prefixed", () => {
    const { finalize, sending } = setup();

    finalize({
      runId: "run-42",
      sessionKey: SESSION_KEY,
      lastAssistantMessage: ANSWER,
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
    const { finalize, sending } = setup();

    finalize({
      runId: "run-43",
      sessionKey: SESSION_KEY,
      lastAssistantMessage: ANSWER,
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
    const { finalize, sending } = setup();

    finalize({ runId: ANNOUNCE_RUN_ID, sessionKey: subagentSession, lastAssistantMessage: ANSWER });

    expect(sending({ content: CHILD_REPORT }, { sessionKey: subagentSession })).toBeUndefined();
  });

  it("falls back to the hook context for the session key", () => {
    const { finalize, sending } = setup();

    finalize({ runId: ANNOUNCE_RUN_ID, lastAssistantMessage: ANSWER }, { sessionKey: SESSION_KEY });

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
