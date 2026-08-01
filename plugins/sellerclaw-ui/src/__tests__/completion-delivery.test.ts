import { describe, expect, it, vi } from "vitest";

import { registerCompletionDeliveryGuard } from "../completion-delivery.js";
import type { OpenClawPluginApi } from "openclaw/plugin-sdk/core";

const CHAT_ID = "48729abf-b759-46bf-8802-6768891edc7e";
const SESSION_KEY = `agent:supervisor:sellerclaw-ui:direct:${CHAT_ID}`;
const ANNOUNCE_RUN_ID =
  "announce:v1:agent:ebay:subagent:cd0df525-f55c-45a9-b328-02baaa8ece25:a5aada61";

type HookHandler = (event: Record<string, unknown>, ctx?: Record<string, unknown>) => unknown;

function buildApi(): { api: OpenClawPluginApi; getHandler: () => HookHandler; warn: ReturnType<typeof vi.fn> } {
  const on = vi.fn();
  const warn = vi.fn();
  const api = {
    config: {},
    logger: { info: vi.fn(), warn, error: vi.fn() },
    registerHttpRoute: vi.fn(),
    on,
  } as unknown as OpenClawPluginApi;
  return {
    api,
    getHandler: () => {
      const call = on.mock.calls[0];
      expect(call?.[0]).toBe("before_agent_finalize");
      return call![1] as HookHandler;
    },
    warn,
  };
}

function runGuard(event: Record<string, unknown>, ctx?: Record<string, unknown>) {
  const { api, getHandler, warn } = buildApi();
  registerCompletionDeliveryGuard(api);
  return { result: getHandler()(event, ctx), warn };
}

describe("completion delivery guard", () => {
  it("asks for a message-tool send when a completion run answered with plain text", () => {
    const { result, warn } = runGuard({
      runId: ANNOUNCE_RUN_ID,
      sessionKey: SESSION_KEY,
      lastAssistantMessage: "Листинг снят с продажи.",
    });

    expect(result).toEqual({
      action: "revise",
      retry: {
        instruction: expect.stringContaining(`target "sellerclaw-ui:direct:${CHAT_ID}"`),
        maxAttempts: 2,
        idempotencyKey: "sellerclaw-ui:completion-requires-message-tool",
      },
    });
    const instruction = (result as { retry: { instruction: string } }).retry.instruction;
    expect(instruction).toContain("`message`");
    // The already-sent branch keeps a compliant turn from double-posting.
    expect(instruction).toContain("NO_REPLY");
    expect(warn).toHaveBeenCalledOnce();
  });

  it("tells the run what to send, not merely to send it", () => {
    // The rescued run is the one that already drifted: a supervisor that spent its turn on
    // bookkeeping answered with its own paperwork. Asking only for delivery posted that verbatim.
    const { result } = runGuard({
      runId: ANNOUNCE_RUN_ID,
      sessionKey: SESSION_KEY,
      lastAssistantMessage:
        "Task complete. **Agent task** `dfcff8eb` is now in `pending_review` status.",
    });

    const instruction = (result as { retry: { instruction: string } }).retry.instruction;
    expect(instruction).toContain("the language they write in");
    expect(instruction).toContain("pending_review");
    expect(instruction).toContain("bare UUIDs");
    expect(instruction).toContain("rewrite it first");
  });

  it("recognizes a completion run from the trigger text when the run id is not prefixed", () => {
    const { result } = runGuard({
      runId: "run-42",
      sessionKey: SESSION_KEY,
      lastAssistantMessage: "Готово.",
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

    expect(result).toMatchObject({ action: "revise" });
  });

  it("leaves a live chat turn alone even when an older completion trigger is in history", () => {
    const { result, warn } = runGuard({
      runId: "run-43",
      sessionKey: SESSION_KEY,
      lastAssistantMessage: "Готово. Листинг снят с продажи.",
      messages: [
        { role: "user", content: "A background task completed. Use this result to reply." },
        { role: "assistant", content: [{ type: "text", text: "Снял." }] },
        { role: "user", content: "ну что?" },
      ],
    });

    expect(result).toBeUndefined();
    expect(warn).not.toHaveBeenCalled();
  });

  it("ignores completions whose requester is not a sellerclaw-ui chat", () => {
    const { result } = runGuard({
      runId: ANNOUNCE_RUN_ID,
      sessionKey: "agent:ebay:subagent:cd0df525-f55c-45a9-b328-02baaa8ece25",
      lastAssistantMessage: "done",
    });

    expect(result).toBeUndefined();
  });

  it("falls back to the hook context for the session key", () => {
    const { result } = runGuard(
      { runId: ANNOUNCE_RUN_ID, lastAssistantMessage: "Листинг снят." },
      { sessionKey: SESSION_KEY },
    );

    expect(result).toMatchObject({ action: "revise" });
  });

  it("stays out of the way when the turn produced no visible answer", () => {
    const { result } = runGuard({
      runId: ANNOUNCE_RUN_ID,
      sessionKey: SESSION_KEY,
      lastAssistantMessage: "   ",
    });

    expect(result).toBeUndefined();
  });

  it("registers nothing when the runtime exposes no hook API", () => {
    const api = {
      config: {},
      logger: { info: vi.fn(), warn: vi.fn(), error: vi.fn() },
      registerHttpRoute: vi.fn(),
    } as unknown as OpenClawPluginApi;

    expect(() => registerCompletionDeliveryGuard(api)).not.toThrow();
  });
});
