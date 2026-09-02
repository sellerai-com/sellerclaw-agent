import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { OpenClawConfig, OpenClawPluginApi } from "openclaw/plugin-sdk/core";

import { sellerclawUiChannelPlugin } from "../channel.js";
import { __resetRelayedThinking, registerReasoningRelay } from "../reasoning-relay.js";
import type { ScwUiAccount } from "../send.js";
import { __resetSubagentOrigins } from "../subagent-origins.js";

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
  const fire = (name: string) => (event: Record<string, unknown>, ctx?: Record<string, unknown>) => {
    for (const handler of hooks.get(name) ?? []) handler(event, ctx);
  };
  return { api, agentEnd: fire("agent_end"), subagentSpawned: fire("subagent_spawned") };
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
    __resetRelayedThinking();
    fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 200 }));
    globalThis.fetch = fetchMock as unknown as typeof fetch;
  });

  afterEach(() => {
    __resetRelayedThinking();
    globalThis.fetch = originalFetch;
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
    // No parent: the supervisor's own thinking is the top level of the panel, never a folded block.
    expect(thoughts.every((c) => c.body.parent_agent_id === undefined)).toBe(true);
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

  it("opens no turn for a run that declined to answer", async () => {
    // A duplicate completion event wakes the supervisor to conclude it has already answered. That
    // turn's thinking is about the plumbing, and belongs nowhere near the answer it had no part in.
    const { agentEnd } = setup();

    agentEnd(
      {
        runId: `${SETTLE_RUN_ID}:retry-2`,
        messages: completionTranscript({
          role: "assistant",
          content: [
            { type: "thinking", thinking: "This is a duplicate completion event. I already replied." },
            { type: "text", text: "NO_REPLY" },
          ],
        }),
      },
      { sessionKey: SESSION_KEY },
    );

    await new Promise((resolve) => setTimeout(resolve, 5));
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("still reports a run whose answer merely mentions the silent token", async () => {
    const { agentEnd } = setup();

    agentEnd(
      {
        runId: SETTLE_RUN_ID,
        messages: completionTranscript({
          role: "assistant",
          content: [
            { type: "thinking", thinking: "Объясняю владельцу, что значит NO_REPLY." },
            { type: "text", text: "NO_REPLY — это служебный ответ агента." },
          ],
        }),
      },
      { sessionKey: SESSION_KEY },
    );

    await new Promise((resolve) => setTimeout(resolve, 5));
    expect(fetchMock).toHaveBeenCalled();
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

describe("a specialist's reasoning", () => {
  const CHILD_SESSION_KEY = "agent:marketing:subagent:9d1a1f0c-6b3f-4a5c-9f0e-2b7d5c1e4a88";
  const originalFetch = globalThis.fetch;
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    __resetSubagentOrigins();
    __resetRelayedThinking();
    fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 200 }));
    globalThis.fetch = fetchMock as unknown as typeof fetch;
  });

  afterEach(() => {
    __resetSubagentOrigins();
    __resetRelayedThinking();
    globalThis.fetch = originalFetch;
  });

  /** The runtime's announcement that the supervisor handed work to a specialist. */
  function spawn(
    subagentSpawned: (event: Record<string, unknown>, ctx?: Record<string, unknown>) => void,
    over: {
      childSessionKey?: string;
      agentId?: string;
      requesterSessionKey?: string;
      to?: string;
    } = {},
  ): void {
    const requesterSessionKey = over.requesterSessionKey ?? SESSION_KEY;
    subagentSpawned(
      {
        childSessionKey: over.childSessionKey ?? CHILD_SESSION_KEY,
        agentId: over.agentId ?? "marketing",
        label: "Ad Performance Review",
        requester: { channel: "sellerclaw-ui", to: over.to ?? `sellerclaw-ui:direct:${CHAT_ID}` },
      },
      {
        runId: "spawn-1",
        childSessionKey: over.childSessionKey ?? CHILD_SESSION_KEY,
        requesterSessionKey,
      },
    );
  }

  /** A specialist's own run: an ordinary dispatched turn, not a completion one. */
  function specialistTranscript(...rounds: Record<string, unknown>[]): unknown[] {
    return [{ role: "user", content: "Проверь расходы на рекламу за неделю." }, ...rounds];
  }

  it("lands in the chat that asked for the work, folded under the agent that delegated", async () => {
    const { agentEnd, subagentSpawned } = setup();
    spawn(subagentSpawned);

    agentEnd(
      {
        runId: "child-run-1",
        messages: specialistTranscript(
          assistant("Тяну расходы Google Ads и Meta за 7 дней."),
          assistant("Ни одна кампания не откручивается — показов ноль."),
        ),
      },
      { sessionKey: CHILD_SESSION_KEY, agentId: "marketing" },
    );
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(4));

    const posted = calls(fetchMock);
    const thoughts = posted.filter((c) => c.url.endsWith("/internal/openclaw/thought"));
    expect(thoughts.map((c) => c.body.text)).toEqual([
      "Тяну расходы Google Ads и Meta за 7 дней.",
      "Ни одна кампания не откручивается — показов ноль.",
    ]);
    expect(thoughts.every((c) => c.body.agent_id === "marketing")).toBe(true);
    expect(thoughts.every((c) => c.body.parent_agent_id === "supervisor")).toBe(true);
    expect(thoughts.every((c) => c.body.chat_id === CHAT_ID)).toBe(true);
    // Addressed with the chat's session: the child's own key encodes no chat at all.
    expect(thoughts.every((c) => c.body.session_key === SESSION_KEY)).toBe(true);
  });

  it("says nothing for a specialist run it never saw spawned", async () => {
    const { agentEnd } = setup();

    agentEnd(
      { runId: "child-run-1", messages: specialistTranscript(assistant("Работаю.")) },
      { sessionKey: CHILD_SESSION_KEY, agentId: "marketing" },
    );

    await new Promise((resolve) => setTimeout(resolve, 5));
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("ignores a spawn that belongs to no chat of ours", async () => {
    const { agentEnd, subagentSpawned } = setup();
    spawn(subagentSpawned, {
      requesterSessionKey: "agent:supervisor:cron:daily-scan:run:17",
      to: "cron:daily-scan",
    });

    agentEnd(
      { runId: "child-run-1", messages: specialistTranscript(assistant("Ночная работа.")) },
      { sessionKey: CHILD_SESSION_KEY, agentId: "marketing" },
    );

    await new Promise((resolve) => setTimeout(resolve, 5));
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("keeps a specialist that declined to answer out of the panel", async () => {
    const { agentEnd, subagentSpawned } = setup();
    spawn(subagentSpawned);

    agentEnd(
      {
        runId: "child-run-1",
        messages: specialistTranscript({
          role: "assistant",
          content: [
            { type: "thinking", thinking: "Отвечать тут нечего." },
            { type: "text", text: "NO_REPLY" },
          ],
        }),
      },
      { sessionKey: CHILD_SESSION_KEY, agentId: "marketing" },
    );

    await new Promise((resolve) => setTimeout(resolve, 5));
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("follows a specialist that delegates further, under the specialist that asked", async () => {
    const { agentEnd, subagentSpawned } = setup();
    const GRANDCHILD = "agent:scout:subagent:0f6b2a71-4c19-4a52-9d0a-8f4c2b6e1d33";
    spawn(subagentSpawned);
    spawn(subagentSpawned, {
      childSessionKey: GRANDCHILD,
      agentId: "scout",
      requesterSessionKey: CHILD_SESSION_KEY,
      to: CHILD_SESSION_KEY,
    });

    agentEnd(
      { runId: "grandchild-run", messages: specialistTranscript(assistant("Смотрю рынок.")) },
      { sessionKey: GRANDCHILD, agentId: "scout" },
    );
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));

    const thoughts = calls(fetchMock).filter((c) => c.url.endsWith("/thought"));
    expect(thoughts.map((c) => c.body.agent_id)).toEqual(["scout"]);
    expect(thoughts.map((c) => c.body.parent_agent_id)).toEqual(["marketing"]);
    expect(thoughts.every((c) => c.body.chat_id === CHAT_ID)).toBe(true);
  });

  it("does not repeat itself when the same run ends twice", async () => {
    // `agent_end` fires per finalized attempt, and the transcript of the second holds what the
    // first already reported.
    const { agentEnd, subagentSpawned } = setup();
    spawn(subagentSpawned);

    agentEnd(
      { runId: "child-run-1", messages: specialistTranscript(assistant("Тяну расходы.")) },
      { sessionKey: CHILD_SESSION_KEY, agentId: "marketing" },
    );
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
    agentEnd(
      {
        runId: "child-run-1",
        messages: specialistTranscript(assistant("Тяну расходы."), assistant("Свожу выводы.")),
      },
      { sessionKey: CHILD_SESSION_KEY, agentId: "marketing" },
    );
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(6));

    const thoughts = calls(fetchMock).filter((c) => c.url.endsWith("/thought"));
    expect(thoughts.map((c) => c.body.text)).toEqual(["Тяну расходы.", "Свожу выводы."]);
  });

  it("reports every turn of a session the supervisor keeps using", async () => {
    const { agentEnd, subagentSpawned } = setup();
    spawn(subagentSpawned);

    agentEnd(
      { runId: "child-run-1", messages: specialistTranscript(assistant("Первый заход.")) },
      { sessionKey: CHILD_SESSION_KEY, agentId: "marketing" },
    );
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
    agentEnd(
      { runId: "child-run-2", messages: specialistTranscript(assistant("Уточняю по Meta.")) },
      { sessionKey: CHILD_SESSION_KEY, agentId: "marketing" },
    );
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(6));

    const thoughts = calls(fetchMock).filter((c) => c.url.endsWith("/thought"));
    expect(thoughts.map((c) => c.body.text)).toEqual(["Первый заход.", "Уточняю по Meta."]);
    // Each run gets its own placeholder turn, so the cloud adopts them independently.
    expect(new Set(thoughts.map((c) => c.body.message_id)).size).toBe(2);
  });
});

describe("outbound sends", () => {
  const originalFetch = globalThis.fetch;
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 200 }));
    globalThis.fetch = fetchMock as unknown as typeof fetch;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  it("gives every send its own self-contained message", async () => {
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

    expect(second.messageId).not.toBe(first.messageId);
    // Each send opens, fills and closes its own turn — the reasoning of the dispatch that is
    // still running is reunited with the answer cloud-side, not by delivering into its message.
    expect(calls(fetchMock).map((c) => c.url)).toEqual([
      "https://api.example.com/internal/openclaw/turn",
      `https://api.example.com/internal/openclaw/turn/${first.messageId}/part`,
      `https://api.example.com/internal/openclaw/turn/${first.messageId}/end`,
      "https://api.example.com/internal/openclaw/turn",
      `https://api.example.com/internal/openclaw/turn/${second.messageId}/part`,
      `https://api.example.com/internal/openclaw/turn/${second.messageId}/end`,
    ]);
  });
});
