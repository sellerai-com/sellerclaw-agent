import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { OpenClawConfig, OpenClawPluginApi } from "openclaw/plugin-sdk/core";

import { sellerclawUiChannelPlugin } from "../channel.js";
import { registerReasoningRelay } from "../reasoning-relay.js";
import type { ScwUiAccount } from "../send.js";
import { __resetTurnBindings, bindInboundTurn, markBoundTurnStreamed } from "../turn-binding.js";

const CHAT_ID = "48729abf-b759-46bf-8802-6768891edc7e";
const SESSION_KEY = `agent:supervisor:sellerclaw-ui:direct:${CHAT_ID}`;
const SETTLE_RUN_ID = `announce:requester-settle:supervisor:${SESSION_KEY}:yield-1`;

const account: ScwUiAccount = {
  apiBaseUrl: "https://api.example.com",
  userId: "550e8400-e29b-41d4-a716-446655440000",
  agentApiKey: "sca-agent-key",
  internalWebhookSecret: "hooks-delivery-token",
  localAgentBaseUrl: "http://127.0.0.1:8001",
};

const config = {
  channels: {
    "sellerclaw-ui": {
      apiBaseUrl: account.apiBaseUrl,
      userId: account.userId,
      agentApiKey: account.agentApiKey,
      internalWebhookSecret: account.internalWebhookSecret,
    },
  },
} as OpenClawConfig;

type HookHandler = (event: Record<string, unknown>, ctx?: Record<string, unknown>) => unknown;

type PluginOutbound = {
  outbound: { sendText: (p: unknown) => Promise<{ messageId: string }> };
};

/** One assistant message as it sits in the run transcript. */
function assistant(...thinking: string[]): Record<string, unknown> {
  return {
    role: "assistant",
    content: [
      ...thinking.map((text) => ({ type: "thinking", thinking: text })),
      { type: "text", text: "Готово." },
    ],
  };
}

/** A completion run's transcript: an earlier exchange, the trigger, then this run's rounds. */
function completionTranscript(...rounds: Record<string, unknown>[]): unknown[] {
  return [
    { role: "user", content: "опубликуй на shopify" },
    assistant("Мысли прошлого прогона."),
    { role: "user", content: [{ type: "text", text: "A background task completed." }] },
    ...rounds,
  ];
}

function setup() {
  const hooks = new Map<string, HookHandler[]>();
  const api = {
    config,
    logger: { info: vi.fn(), warn: vi.fn(), error: vi.fn() },
    on: (name: string, handler: HookHandler) => {
      const list = hooks.get(name) ?? [];
      list.push(handler);
      hooks.set(name, list);
    },
  } as unknown as OpenClawPluginApi;
  registerReasoningRelay(api);
  const agentEnd = (event: Record<string, unknown>, ctx?: Record<string, unknown>) => {
    for (const handler of hooks.get("agent_end") ?? []) handler(event, ctx);
  };
  return { api, agentEnd };
}

/** Bodies of every webhook call, paired with the endpoint they hit. */
function calls(fetchMock: ReturnType<typeof vi.fn>): Array<{
  url: string;
  body: Record<string, unknown>;
}> {
  return fetchMock.mock.calls.map((call) => ({
    url: String(call[0]),
    body: JSON.parse((call[1] as RequestInit).body as string) as Record<string, unknown>,
  }));
}

describe("reasoning relay", () => {
  const originalFetch = globalThis.fetch;
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    __resetTurnBindings();
    fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 200 }));
    globalThis.fetch = fetchMock as unknown as typeof fetch;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    __resetTurnBindings();
  });

  it("reports a completion run's thinking as an empty turn the cloud hands to the answer", async () => {
    const { agentEnd } = setup();

    agentEnd(
      {
        runId: SETTLE_RUN_ID,
        messages: completionTranscript(
          assistant("Смотрю, что сделал специалист."),
          assistant("Собираю ответ владельцу."),
        ),
      },
      { sessionKey: SESSION_KEY, agentId: "supervisor" },
    );
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(4));

    const posted = calls(fetchMock);
    const thoughts = posted.filter((c) => c.url.endsWith("/internal/openclaw/thought"));
    expect(thoughts.map((c) => c.body.text)).toEqual([
      "Смотрю, что сделал специалист.",
      "Собираю ответ владельцу.",
    ]);
    expect(thoughts.map((c) => c.body.seq)).toEqual([0, 1]);
    expect(thoughts.every((c) => c.body.chat_id === CHAT_ID)).toBe(true);
    expect(thoughts.every((c) => c.body.agent_id === "supervisor")).toBe(true);
    const messageId = thoughts[0].body.message_id as string;
    expect(new Set(thoughts.map((c) => c.body.message_id))).toEqual(new Set([messageId]));

    // The turn is opened and closed with no parts: the cloud drops that message and hands the
    // reasoning to this exchange's answer.
    expect(posted.slice(2).map((c) => c.url)).toEqual([
      "https://api.example.com/internal/openclaw/turn",
      `https://api.example.com/internal/openclaw/turn/${messageId}/end`,
    ]);
  });

  it("reports only the current run's thinking, not the whole transcript", async () => {
    const { agentEnd } = setup();

    agentEnd(
      { runId: SETTLE_RUN_ID, messages: completionTranscript(assistant("Только этот прогон.")) },
      { sessionKey: SESSION_KEY },
    );
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));

    const thoughts = calls(fetchMock).filter((c) => c.url.endsWith("/thought"));
    expect(thoughts.map((c) => c.body.text)).toEqual(["Только этот прогон."]);
  });

  it("stays silent for a live chat turn, which streams its own reasoning", async () => {
    const { agentEnd } = setup();

    agentEnd(
      {
        runId: "run-42",
        messages: [{ role: "user", content: "найди стаканы" }, assistant("Ищу.")],
      },
      { sessionKey: SESSION_KEY },
    );

    await new Promise((resolve) => setTimeout(resolve, 5));
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each([
    ["another channel's session", { sessionKey: "agent:supervisor:telegram:direct:42" }],
    ["no session at all", {}],
  ])("ignores %s", async (_label, ctx) => {
    const { agentEnd } = setup();

    agentEnd({ runId: SETTLE_RUN_ID, messages: completionTranscript(assistant("Не наше.")) }, ctx);

    await new Promise((resolve) => setTimeout(resolve, 5));
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("opens no turn for a completion run that produced no thinking", async () => {
    const { agentEnd } = setup();

    agentEnd(
      {
        runId: SETTLE_RUN_ID,
        messages: completionTranscript({
          role: "assistant",
          content: [{ type: "text", text: "Готово." }],
        }),
      },
      { sessionKey: SESSION_KEY },
    );

    await new Promise((resolve) => setTimeout(resolve, 5));
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("recognizes a completion run from the trigger text when the run id is not prefixed", async () => {
    const { agentEnd } = setup();

    agentEnd(
      { runId: "run-77", messages: completionTranscript(assistant("Догоняю задачу.")) },
      { sessionKey: SESSION_KEY },
    );
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));

    const thoughts = calls(fetchMock).filter((c) => c.url.endsWith("/thought"));
    expect(thoughts.map((c) => c.body.text)).toEqual(["Догоняю задачу."]);
  });
});

describe("outbound sends and the dispatch's turn", () => {
  const originalFetch = globalThis.fetch;
  let fetchMock: ReturnType<typeof vi.fn>;
  const DISPATCH_MESSAGE_ID = "9f1b7c2e-5d4a-4b8c-9e3f-0a1b2c3d4e5f";

  beforeEach(() => {
    __resetTurnBindings();
    fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 200 }));
    globalThis.fetch = fetchMock as unknown as typeof fetch;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    __resetTurnBindings();
  });

  it("delivers into the dispatch's message and leaves the turn open for finishTurn", async () => {
    bindInboundTurn(SESSION_KEY, DISPATCH_MESSAGE_ID, CHAT_ID);
    const plugin = sellerclawUiChannelPlugin as unknown as PluginOutbound;

    const result = await plugin.outbound.sendText({
      account,
      sessionKey: SESSION_KEY,
      text: "Запускаю публикацию.",
    });

    expect(result.messageId).toBe(DISPATCH_MESSAGE_ID);
    // Start (idempotent) and the part, but no ``end``: the dispatch owns finalization.
    expect(calls(fetchMock).map((c) => c.url)).toEqual([
      "https://api.example.com/internal/openclaw/turn",
      `https://api.example.com/internal/openclaw/turn/${DISPATCH_MESSAGE_ID}/part`,
    ]);
  });

  it("gives a second send in the same turn its own message", async () => {
    bindInboundTurn(SESSION_KEY, DISPATCH_MESSAGE_ID, CHAT_ID);
    const plugin = sellerclawUiChannelPlugin as unknown as PluginOutbound;

    const first = await plugin.outbound.sendText({
      account,
      sessionKey: SESSION_KEY,
      text: "Работаю.",
    });
    const second = await plugin.outbound.sendText({
      account,
      sessionKey: SESSION_KEY,
      text: "Готово.",
    });

    expect(first.messageId).toBe(DISPATCH_MESSAGE_ID);
    expect(second.messageId).not.toBe(DISPATCH_MESSAGE_ID);
    // The self-contained second message closes itself.
    expect(calls(fetchMock).some((c) => c.url.endsWith(`/turn/${second.messageId}/end`))).toBe(
      true,
    );
  });

  it("stays a separate message once the dispatch streamed a reply of its own", async () => {
    bindInboundTurn(SESSION_KEY, DISPATCH_MESSAGE_ID, CHAT_ID);
    markBoundTurnStreamed(SESSION_KEY);
    const plugin = sellerclawUiChannelPlugin as unknown as PluginOutbound;

    const result = await plugin.outbound.sendText({
      account,
      sessionKey: SESSION_KEY,
      text: "Отдельная реплика.",
    });

    expect(result.messageId).not.toBe(DISPATCH_MESSAGE_ID);
    expect(calls(fetchMock).map((c) => c.url)).toEqual([
      "https://api.example.com/internal/openclaw/turn",
      `https://api.example.com/internal/openclaw/turn/${result.messageId}/part`,
      `https://api.example.com/internal/openclaw/turn/${result.messageId}/end`,
    ]);
  });

  it("stops offering a message whose dispatch never closed it", async () => {
    vi.useFakeTimers();
    try {
      bindInboundTurn(SESSION_KEY, DISPATCH_MESSAGE_ID, CHAT_ID);
      vi.advanceTimersByTime(11 * 60_000);
      const plugin = sellerclawUiChannelPlugin as unknown as PluginOutbound;

      const result = await plugin.outbound.sendText({
        account,
        sessionKey: SESSION_KEY,
        text: "Много позже.",
      });

      expect(result.messageId).not.toBe(DISPATCH_MESSAGE_ID);
      expect(calls(fetchMock).some((c) => c.url.endsWith(`/turn/${result.messageId}/end`))).toBe(
        true,
      );
    } finally {
      vi.useRealTimers();
    }
  });
});
