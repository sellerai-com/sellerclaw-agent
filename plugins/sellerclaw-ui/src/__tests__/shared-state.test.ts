import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { OpenClawPluginApi } from "openclaw/plugin-sdk/core";

const deliverTextToChat = vi.fn().mockResolvedValue({ messageId: "delivered" });

vi.mock("../channel.js", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../channel.js")>();
  return {
    ...actual,
    deliverTextToChat,
    resolveSellerclawUiAccount: () => ({
      apiBaseUrl: "http://api.test",
      userId: "user",
      agentApiKey: "key",
      internalWebhookSecret: "secret",
      localAgentBaseUrl: "http://127.0.0.1:8001",
    }),
  };
});

/**
 * OpenClaw re-evaluates the plugin module on every registry pass. These tests simulate that with
 * `vi.resetModules()`: hooks registered by a LATER module instance must still see state captured
 * by an EARLIER one, otherwise the completion guard silently stops suppressing the raw subagent
 * envelope whenever a registry rebuild lands between capture and delivery.
 */

const CHAT_ID = "48729abf-b759-46bf-8802-6768891edc7e";
const SESSION_KEY = `agent:supervisor:sellerclaw-ui:direct:${CHAT_ID}`;
const ANNOUNCE_RUN_ID =
  "announce:v1:agent:ebay:subagent:cd0df525-f55c-45a9-b328-02baaa8ece25:a5aada61";
const CHILD_REPORT = "Task `dfcff8eb` completed and handed back for review (`pending_review`).";
const ANSWER = "Готово: листинг снят с продажи.";

type HookHandler = (event: Record<string, unknown>, ctx?: Record<string, unknown>) => unknown;

function buildApi(): { api: OpenClawPluginApi; hooks: Map<string, HookHandler[]> } {
  const hooks = new Map<string, HookHandler[]>();
  const api = {
    config: {},
    logger: { info: vi.fn(), warn: vi.fn(), error: vi.fn() },
    on: (name: string, handler: HookHandler) => {
      const list = hooks.get(name) ?? [];
      list.push(handler);
      hooks.set(name, list);
    },
  } as unknown as OpenClawPluginApi;
  return { api, hooks };
}

function fire(hooks: Map<string, HookHandler[]>, name: string, event: Record<string, unknown>, ctx?: Record<string, unknown>) {
  let last: unknown;
  for (const handler of hooks.get(name) ?? []) last = handler(event, ctx);
  return last;
}

/** Loads a FRESH module instance of the guard, as a new registry pass would. */
async function loadFreshPass() {
  vi.resetModules();
  const mod = await import("../completion-delivery.js");
  const { api, hooks } = buildApi();
  mod.registerCompletionDeliveryGuard(api);
  return { hooks };
}

beforeEach(async () => {
  vi.resetModules();
  const { __resetSharedState } = await import("../shared-state.js");
  __resetSharedState();
  deliverTextToChat.mockClear();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("guard state across module re-instantiation", () => {
  it("substitutes the answer even when the delivery hook comes from a later module instance", async () => {
    const first = await loadFreshPass();
    fire(
      first.hooks,
      "before_agent_finalize",
      { runId: ANNOUNCE_RUN_ID, sessionKey: SESSION_KEY, lastAssistantMessage: ANSWER },
      { sessionKey: SESSION_KEY },
    );

    // A registry rebuild lands here: a new module instance registers the live hooks.
    const second = await loadFreshPass();
    const result = fire(
      second.hooks,
      "message_sending",
      { to: CHAT_ID, content: CHILD_REPORT },
      { sessionKey: SESSION_KEY },
    ) as { content?: string; cancel?: boolean } | undefined;

    expect(result?.content).toBe(ANSWER);
    expect(result?.cancel).toBeUndefined();
  });

  it("still suppresses the raw envelope across instances when the run produced no answer", async () => {
    const first = await loadFreshPass();
    fire(
      first.hooks,
      "agent_end",
      { runId: ANNOUNCE_RUN_ID },
      { sessionKey: SESSION_KEY },
    );

    const second = await loadFreshPass();
    const result = fire(
      second.hooks,
      "message_sending",
      { to: CHAT_ID, content: CHILD_REPORT },
      { sessionKey: SESSION_KEY },
    ) as { cancel?: boolean } | undefined;

    expect(result?.cancel).toBe(true);
  });

  it("carries the messaged-owner mark across instances, so a compliant run is left alone", async () => {
    const first = await loadFreshPass();
    fire(
      first.hooks,
      "before_tool_call",
      { toolName: "message", params: { action: "send" }, runId: ANNOUNCE_RUN_ID },
      { sessionKey: SESSION_KEY },
    );

    const second = await loadFreshPass();
    fire(
      second.hooks,
      "before_agent_finalize",
      { runId: ANNOUNCE_RUN_ID, sessionKey: SESSION_KEY, lastAssistantMessage: ANSWER },
      { sessionKey: SESSION_KEY },
    );
    const result = fire(
      second.hooks,
      "message_sending",
      { to: CHAT_ID, content: "какой-то обычный текст" },
      { sessionKey: SESSION_KEY },
    );

    // The run messaged the owner itself: nothing pending, nothing rewritten or cancelled.
    expect(result).toBeUndefined();
  });
});

describe("getSharedState", () => {
  it("returns the same object for the same key across module instances", async () => {
    vi.resetModules();
    const a = await import("../shared-state.js");
    const mapA = a.getSharedState("probe", () => new Map<string, number>());
    mapA.set("x", 1);

    vi.resetModules();
    const b = await import("../shared-state.js");
    const mapB = b.getSharedState("probe", () => new Map<string, number>());

    expect(mapB).toBe(mapA);
    expect(mapB.get("x")).toBe(1);
  });

  it("keeps independent keys apart and honours the reset", async () => {
    const { getSharedState, __resetSharedState } = await import("../shared-state.js");
    const one = getSharedState("a", () => ({ value: 1 }));
    const two = getSharedState("b", () => ({ value: 2 }));
    expect(one).not.toBe(two);
    __resetSharedState();
    expect(getSharedState("a", () => ({ value: 9 })).value).toBe(9);
  });
});

describe("pass counter across module re-instantiation", () => {
  it("keeps counting so the liveness line does not report pass #1 forever", async () => {
    vi.resetModules();
    const first = await import("../hook-registration.js");
    const apiA = buildApi();
    first.registerLifecycleHooks(apiA.api);

    vi.resetModules();
    const second = await import("../hook-registration.js");
    const apiB = buildApi();
    second.registerLifecycleHooks(apiB.api);

    expect(apiA.api.logger.warn).toHaveBeenCalledWith(
      "sellerclaw-ui: lifecycle hooks registered (pass #1)",
    );
    expect(apiB.api.logger.warn).toHaveBeenCalledWith(
      "sellerclaw-ui: lifecycle hooks registered (pass #2)",
    );
  });
});

describe("run-outcome state across module re-instantiation", () => {
  it("lets the inbound turn read a verdict recorded by a later module instance", async () => {
    // The `agent_end` hook fires from the newest pass; the turn that reads the verdict lives in
    // the instance whose HTTP route was registered on the first pass.
    vi.resetModules();
    const writerPass = await import("../run-outcome.js");
    const { api, hooks } = buildApi();
    writerPass.registerRunOutcomeTracker(api);
    fire(
      hooks,
      "agent_end",
      { runId: "b0d1a2c3-0000-4000-8000-000000000000", success: false, error: "billing declined" },
      { sessionKey: SESSION_KEY },
    );

    vi.resetModules();
    const readerPass = await import("../run-outcome.js");
    const outcome = readerPass.readRunOutcome(SESSION_KEY);

    expect(outcome).toEqual({ success: false, hasError: true });
  });

  it("carries an owner stop across instances", async () => {
    vi.resetModules();
    const writerPass = await import("../run-outcome.js");
    writerPass.markOwnerAbort(SESSION_KEY);

    vi.resetModules();
    const readerPass = await import("../run-outcome.js");
    expect(readerPass.consumeOwnerAbort(SESSION_KEY)).toBe(true);
  });
});
