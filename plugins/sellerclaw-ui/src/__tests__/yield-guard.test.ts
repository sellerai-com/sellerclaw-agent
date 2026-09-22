import { beforeEach, describe, expect, it, vi } from "vitest";
import type { OpenClawPluginApi } from "openclaw/plugin-sdk/core";

import {
  NOTHING_TO_WAIT_FOR,
  __resetYieldGuardState,
  registerYieldGuard,
  yieldGuardDecision,
} from "../yield-guard.js";

const CHAT_SESSION = "agent:supervisor:sellerclaw-ui:direct:072c4b5d-2871-4721-8bb0-5a96ce9db745";
const SUBAGENT_SESSION = "agent:scout:subagent:2e918949-bc50-49da-a07a-6ce48bf54787";
const BLOCKED = { block: true, blockReason: NOTHING_TO_WAIT_FOR };

type Call = { toolName: string; runId?: string; sessionKey?: string };

function decide(call: Call) {
  return yieldGuardDecision(
    { toolName: call.toolName, runId: call.runId },
    { sessionKey: call.sessionKey ?? CHAT_SESSION },
  );
}

describe("yieldGuardDecision", () => {
  beforeEach(() => {
    __resetYieldGuardState();
  });

  it.each([
    {
      // The status question: the research was spawned by an earlier turn.
      name: "blocks a yield in a run that spawned nothing",
      before: [] as Call[],
      call: { toolName: "sessions_yield", runId: "run-2" },
      expected: BLOCKED,
    },
    {
      name: "blocks a yield when only an earlier run spawned",
      before: [{ toolName: "sessions_spawn", runId: "run-1" }],
      call: { toolName: "sessions_yield", runId: "run-2" },
      expected: BLOCKED,
    },
    {
      name: "lets a run wait on the subagent it just spawned",
      before: [{ toolName: "sessions_spawn", runId: "run-1" }],
      call: { toolName: "sessions_yield", runId: "run-1" },
      expected: undefined,
    },
    {
      name: "leaves a subagent session to the engine",
      before: [],
      call: { toolName: "sessions_yield", runId: "run-3", sessionKey: SUBAGENT_SESSION },
      expected: undefined,
    },
    {
      name: "leaves a call no run can be attributed to",
      before: [],
      call: { toolName: "sessions_yield" },
      expected: undefined,
    },
    {
      name: "ignores other tools",
      before: [],
      call: { toolName: "message", runId: "run-2" },
      expected: undefined,
    },
  ])("$name", ({ before, call, expected }) => {
    for (const earlier of before) decide(earlier);
    expect(decide(call)).toEqual(expected);
  });

  it("takes the run id from the hook context when the event has none", () => {
    yieldGuardDecision({ toolName: "sessions_spawn" }, { sessionKey: CHAT_SESSION, runId: "run-1" });
    expect(
      yieldGuardDecision({ toolName: "sessions_yield" }, { sessionKey: CHAT_SESSION, runId: "run-1" }),
    ).toBeUndefined();
  });
});

describe("registerYieldGuard", () => {
  beforeEach(() => {
    __resetYieldGuardState();
  });

  it("answers the hook with the block and records it in the delivery log", () => {
    const handlers: Array<(event: unknown, ctx?: unknown) => unknown> = [];
    const warn = vi.fn();
    const api = {
      logger: { info: vi.fn(), warn, error: vi.fn() },
      on: (name: string, handler: (event: unknown, ctx?: unknown) => unknown) => {
        if (name === "before_tool_call") handlers.push(handler);
      },
    } as unknown as OpenClawPluginApi;

    registerYieldGuard(api);

    expect(handlers).toHaveLength(1);
    expect(
      handlers[0]!({ toolName: "sessions_yield", runId: "run-2" }, { sessionKey: CHAT_SESSION }),
    ).toEqual(BLOCKED);
    expect(warn.mock.calls.map((c) => String(c[0])).join("\n")).toContain(
      "sessions_yield blocked: nothing spawned in this run run_id=run-2",
    );
  });
});
