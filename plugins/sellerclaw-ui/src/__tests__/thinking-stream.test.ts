import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { OpenClawConfig, OpenClawPluginApi } from "openclaw/plugin-sdk/core";

import { __resetRelayedThinking, registerReasoningRelay } from "../reasoning-relay.js";
import type { ScwUiAccount } from "../send.js";
import { __resetSubagentOrigins, rememberSubagentOrigin } from "../subagent-origins.js";
import {
  __resetThinkingStream,
  ownsRunReasoning,
  registerLiveThinkingStream,
} from "../thinking-stream.js";

const CHAT_ID = "48729abf-b759-46bf-8802-6768891edc7e";
const CHAT_SESSION = `agent:supervisor:sellerclaw-ui:direct:${CHAT_ID}`;
const CHILD_SESSION = "agent:marketing:subagent:6f1c2f6e-6f27-4a1a-9a52-2c0a5f7ac1de";
const CHILD_RUN = "run-child-1";

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

type Event = {
  runId: string;
  stream: string;
  data: Record<string, unknown>;
  sessionKey?: string;
  agentId?: string;
};

type HookHandler = (event: Record<string, unknown>, ctx?: Record<string, unknown>) => unknown;

/** Register the specialist → chat connection the runtime states at `subagent_spawned`. */
function followChild(childSessionKey = CHILD_SESSION): void {
  rememberSubagentOrigin({
    childSessionKey,
    agentId: "marketing",
    requesterSessionKey: CHAT_SESSION,
  });
}

function setup() {
  const hooks = new Map<string, HookHandler[]>();
  let emit: ((event: Event) => void) | null = null;
  const api = {
    config,
    logger: { info: vi.fn(), warn: vi.fn(), error: vi.fn() },
    on: (name: string, handler: HookHandler) => {
      const list = hooks.get(name) ?? [];
      list.push(handler);
      hooks.set(name, list);
    },
    registerAgentEventSubscription: (subscription: {
      streams?: string[];
      handle: (event: Event) => void;
    }) => {
      emit = (event: Event) => {
        if (subscription.streams && !subscription.streams.includes(event.stream)) return;
        subscription.handle(event);
      };
    },
  } as unknown as OpenClawPluginApi;
  registerReasoningRelay(api);
  registerLiveThinkingStream(api);
  if (!emit) throw new Error("subscription was not registered");
  const send = emit as (event: Event) => void;
  return {
    api,
    /** The run's opening lifecycle event, the only stream that carries the session key. */
    start: (runId: string, sessionKey: string) =>
      send({ runId, stream: "lifecycle", data: { phase: "start" }, sessionKey }),
    end: (runId: string) =>
      send({ runId, stream: "lifecycle", data: { phase: "end" }, sessionKey: undefined }),
    /** A thinking event as the native runner emits it: cumulative text plus its delta. */
    think: (runId: string, text: string, delta?: string) =>
      send({ runId, stream: "thinking", data: { text, delta: delta ?? text } }),
    thinkDelta: (runId: string, delta: string) =>
      send({ runId, stream: "thinking", data: { delta } }),
    tick: (runId: string) =>
      send({ runId, stream: "thinking", data: { progressTokens: 128 } }),
    agentEnd: (event: Record<string, unknown>, ctx?: Record<string, unknown>) => {
      for (const handler of hooks.get("agent_end") ?? []) handler(event, ctx);
    },
  };
}

function calls(fetchMock: ReturnType<typeof vi.fn>): Array<{
  url: string;
  body: Record<string, unknown>;
}> {
  return fetchMock.mock.calls.map((call) => ({
    url: String(call[0]),
    body: JSON.parse((call[1] as RequestInit).body as string) as Record<string, unknown>,
  }));
}

function thoughts(fetchMock: ReturnType<typeof vi.fn>) {
  return calls(fetchMock).filter((c) => c.url.endsWith("/internal/openclaw/thought"));
}

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

describe("live thinking stream", () => {
  const originalFetch = globalThis.fetch;
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    __resetThinkingStream();
    __resetSubagentOrigins();
    __resetRelayedThinking();
    fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 200 }));
    globalThis.fetch = fetchMock as unknown as typeof fetch;
  });

  afterEach(() => {
    __resetThinkingStream();
    __resetSubagentOrigins();
    __resetRelayedThinking();
    globalThis.fetch = originalFetch;
  });

  it("reports a specialist's thinking to the chat it works for, then closes an empty turn", async () => {
    followChild();
    const { start, think, end } = setup();

    start(CHILD_RUN, CHILD_SESSION);
    think(CHILD_RUN, "Смотрю конкурентов.");
    think(CHILD_RUN, "Смотрю конкурентов.\nПишу описание.");
    end(CHILD_RUN);
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));

    const posted = calls(fetchMock);
    const items = thoughts(fetchMock);
    expect(items.map((c) => c.body.text)).toEqual(["Смотрю конкурентов.\nПишу описание."]);
    expect(items[0].body.chat_id).toBe(CHAT_ID);
    expect(items[0].body.session_key).toBe(CHAT_SESSION);
    // Attributed to the specialist and folded under the agent that delegated to it.
    expect(items[0].body.agent_id).toBe("marketing");
    expect(items[0].body.parent_agent_id).toBe("supervisor");

    const messageId = items[0].body.message_id as string;
    expect(posted.slice(1).map((c) => c.url)).toEqual([
      "https://api.example.com/internal/openclaw/turn",
      `https://api.example.com/internal/openclaw/turn/${messageId}/end`,
    ]);
  });

  it("posts while the run is still going once the buffer fills", async () => {
    followChild();
    const { start, think } = setup();
    const first = `${"а".repeat(2100)}\nхвост`;

    start(CHILD_RUN, CHILD_SESSION);
    think(CHILD_RUN, first);

    // No terminal event yet: this post is the point of the whole module.
    await vi.waitFor(() => expect(thoughts(fetchMock)).toHaveLength(1));
    expect(thoughts(fetchMock)[0].body.text).toBe("а".repeat(2100));
    expect(calls(fetchMock).some((c) => c.url.endsWith("/turn"))).toBe(false);
  });

  it("accumulates events that carry only a delta", async () => {
    followChild();
    const { start, thinkDelta, end } = setup();

    start(CHILD_RUN, CHILD_SESSION);
    thinkDelta(CHILD_RUN, "Первая часть. ");
    thinkDelta(CHILD_RUN, "Вторая часть.");
    end(CHILD_RUN);
    await vi.waitFor(() => expect(thoughts(fetchMock)).toHaveLength(1));

    expect(thoughts(fetchMock)[0].body.text).toBe("Первая часть. Вторая часть.");
  });

  it.each([
    {
      id: "a run in the chat's own session — the dispatch and the relay own those",
      sessionKey: CHAT_SESSION,
    },
    {
      id: "a subagent nobody registered — a cron or task run",
      sessionKey: "agent:ops:subagent:11111111-2222-3333-4444-555555555555",
    },
    { id: "a run with no session key at all", sessionKey: "" },
  ])("says nothing for $id", async ({ sessionKey }) => {
    followChild();
    const { start, think, end } = setup();
    const runId = "run-other";

    if (sessionKey) start(runId, sessionKey);
    think(runId, "Мысли не для этого чата.");
    end(runId);

    await new Promise((resolve) => setTimeout(resolve, 10));
    expect(fetchMock).not.toHaveBeenCalled();
    expect(ownsRunReasoning(runId)).toBe(false);
  });

  it("still follows a specialist whose spawn was announced after its first thinking", async () => {
    // Nothing guarantees the `subagent_spawned` hook runs before the child's first thinking
    // event. A "not mine" verdict must not stick for a session that could still get an address.
    const { start, think, end } = setup();

    start(CHILD_RUN, CHILD_SESSION);
    think(CHILD_RUN, "Мысль до того, как стал известен адрес.");
    await new Promise((resolve) => setTimeout(resolve, 10));
    expect(fetchMock).not.toHaveBeenCalled();

    followChild();
    think(CHILD_RUN, "Мысль до того, как стал известен адрес.\nТеперь адрес есть.");
    end(CHILD_RUN);
    await vi.waitFor(() => expect(thoughts(fetchMock)).toHaveLength(1));

    // Nothing is lost: the events carry the running cumulative text, so the run that starts late
    // has never seen a prefix and takes the whole block as its first delta.
    expect(thoughts(fetchMock)[0].body.text).toBe(
      "Мысль до того, как стал известен адрес.\nТеперь адрес есть.",
    );
    expect(thoughts(fetchMock)[0].body.chat_id).toBe(CHAT_ID);
  });

  it("opens no turn for a run that produced no thinking", async () => {
    followChild();
    const { start, tick, end } = setup();

    start(CHILD_RUN, CHILD_SESSION);
    tick(CHILD_RUN);
    end(CHILD_RUN);

    await new Promise((resolve) => setTimeout(resolve, 10));
    expect(fetchMock).not.toHaveBeenCalled();
    expect(ownsRunReasoning(CHILD_RUN)).toBe(false);
  });

  it("keeps the relay quiet about a run it already streamed", async () => {
    followChild();
    const { start, think, end, agentEnd } = setup();

    start(CHILD_RUN, CHILD_SESSION);
    think(CHILD_RUN, "Единственная мысль.");
    end(CHILD_RUN);
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
    expect(ownsRunReasoning(CHILD_RUN)).toBe(true);

    // The transcript holds the same paragraph; the relay must not say it a second time.
    agentEnd(
      { runId: CHILD_RUN, messages: [{ role: "user", content: "go" }, assistant("Единственная мысль.")] },
      { sessionKey: CHILD_SESSION, agentId: "marketing" },
    );
    await new Promise((resolve) => setTimeout(resolve, 10));
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it("closes the turn exactly once when both the lifecycle event and agent_end arrive", async () => {
    followChild();
    const { start, think, end, agentEnd } = setup();

    start(CHILD_RUN, CHILD_SESSION);
    // Short enough to sit in the buffer unposted: this is the case that used to be told twice.
    think(CHILD_RUN, "Мысль.");
    agentEnd(
      { runId: CHILD_RUN, messages: [{ role: "user", content: "go" }, assistant("Мысль.")] },
      { sessionKey: CHILD_SESSION, agentId: "marketing" },
    );
    end(CHILD_RUN);
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));

    await new Promise((resolve) => setTimeout(resolve, 10));
    expect(thoughts(fetchMock).map((c) => c.body.text)).toEqual(["Мысль."]);
    expect(calls(fetchMock).filter((c) => c.url.endsWith("/end"))).toHaveLength(1);
  });

  it("does not install when the host offers no agent-event subscriptions", () => {
    const api = {
      config,
      logger: { info: vi.fn(), warn: vi.fn(), error: vi.fn() },
    } as unknown as OpenClawPluginApi;

    registerLiveThinkingStream(api);

    expect(api.logger.warn).toHaveBeenCalledWith(
      expect.stringContaining("live thinking not installed"),
    );
  });
});
