import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { OpenClawPluginApi } from "openclaw/plugin-sdk/core";

import {
  BRIEF_CACHE_TTL_MS,
  GOAL_GONE_TEXT,
  __resetGoalBrief,
  isCoordinatorSession,
  registerGoalBrief,
} from "../goal-brief.js";

const CHAT = "agent:supervisor:sellerclaw-ui:direct:4df02075-3ac1-4f53-a86e-e3bf7775fa9e";
const OTHER_CHAT = "agent:supervisor:telegram:direct:6148587797";
const CRON_RUN = "agent:supervisor:cron:ca82691c-1f62-4348-a866-613cad18786c:run:5f83a645-6608";
const HOOK_RUN = "agent:supervisor:hook:0b5f3a8e-6c1d-4c1e-9d4f-1f3b2a7c9e10";
const SPECIALIST = "agent:supplier:subagent:ab91e3dd-926b-4a87-a20b-b81882f9cc13";

const CONFIG = {
  channels: {
    "sellerclaw-ui": {
      apiBaseUrl: "https://api.example/",
      userId: "user-1",
      agentApiKey: "sca",
      internalWebhookSecret: "secret",
    },
  },
} as Record<string, unknown>;

type Handler = (event: unknown, ctx?: Record<string, unknown>) => unknown;

function install(): {
  handlers: Record<string, Handler>;
  logger: { info: ReturnType<typeof vi.fn>; warn: ReturnType<typeof vi.fn>; error: ReturnType<typeof vi.fn> };
} {
  const on = vi.fn();
  const logger = { info: vi.fn(), warn: vi.fn(), error: vi.fn() };
  registerGoalBrief({ config: CONFIG, logger, on } as unknown as OpenClawPluginApi);
  const handlers: Record<string, Handler> = {};
  for (const [name, handler] of on.mock.calls) handlers[String(name)] = handler as Handler;
  return { handlers, logger };
}

function briefResponse(text: string | null, fingerprint: string | null): Response {
  return new Response(JSON.stringify({ text, fingerprint }), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

async function turn(handlers: Record<string, Handler>, sessionKey: string): Promise<unknown> {
  return handlers.before_prompt_build({ prompt: "hi", messages: [] }, { sessionKey });
}

describe("isCoordinatorSession", () => {
  it.each([
    [CHAT, "", true],
    [OTHER_CHAT, "", true],
    [CRON_RUN, "", true],
    [HOOK_RUN, "", true],
    [SPECIALIST, "", false],
    [CHAT, "supplier", false],
    ["", "supervisor", false],
  ])("%s (agent %s) -> %s", (sessionKey, agentId, expected) => {
    expect(isCoordinatorSession(sessionKey, agentId)).toBe(expected);
  });
});

describe("registerGoalBrief", () => {
  const originalFetch = globalThis.fetch;
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    __resetGoalBrief();
    fetchMock = vi.fn().mockImplementation(async () => briefResponse("<active_goal>A</active_goal>", "fp-a"));
    globalThis.fetch = fetchMock as unknown as typeof fetch;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.useRealTimers();
  });

  it("asks the cloud for the brief with the agent's key", async () => {
    const { handlers } = install();

    await turn(handlers, CHAT);

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("https://api.example/agent/goals/brief");
    expect(init.method).toBe("GET");
    expect(init.headers).toEqual({ Authorization: "Bearer sca" });
  });

  it("adds the block on a session's first turn and not again while it is unchanged", async () => {
    const { handlers } = install();

    expect(await turn(handlers, CHAT)).toEqual({ prependContext: "<active_goal>A</active_goal>" });
    expect(await turn(handlers, CHAT)).toBeUndefined();
    // Another session has its own history, so it gets the block too.
    expect(await turn(handlers, OTHER_CHAT)).toEqual({ prependContext: "<active_goal>A</active_goal>" });
  });

  it("adds the block again once it changes", async () => {
    vi.useFakeTimers();
    const { handlers } = install();
    await turn(handlers, CHAT);
    fetchMock.mockImplementation(async () => briefResponse("<active_goal>B</active_goal>", "fp-b"));

    vi.advanceTimersByTime(BRIEF_CACHE_TTL_MS + 1);

    expect(await turn(handlers, CHAT)).toEqual({ prependContext: "<active_goal>B</active_goal>" });
  });

  it("serves sessions from one cloud read while it is fresh", async () => {
    const { handlers } = install();

    await Promise.all([turn(handlers, CHAT), turn(handlers, OTHER_CHAT), turn(handlers, CRON_RUN)]);

    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it.each(["after_compaction", "before_reset", "session_end"])(
    "adds the block again after %s",
    async (hook) => {
      const { handlers } = install();
      await turn(handlers, CHAT);

      handlers[hook]({}, { sessionKey: CHAT });

      expect(await turn(handlers, CHAT)).toEqual({ prependContext: "<active_goal>A</active_goal>" });
    },
  );

  it("says once that the goal is gone, then nothing", async () => {
    vi.useFakeTimers();
    const { handlers } = install();
    await turn(handlers, CHAT);
    fetchMock.mockImplementation(async () => briefResponse(null, null));
    vi.advanceTimersByTime(BRIEF_CACHE_TTL_MS + 1);

    expect(await turn(handlers, CHAT)).toEqual({ prependContext: GOAL_GONE_TEXT });
    expect(await turn(handlers, CHAT)).toBeUndefined();
  });

  it("adds nothing to a session that never had a goal", async () => {
    fetchMock.mockImplementation(async () => briefResponse(null, null));
    const { handlers } = install();

    expect(await turn(handlers, CHAT)).toBeUndefined();
  });

  it("leaves a specialist's turns alone", async () => {
    const { handlers } = install();

    expect(await turn(handlers, SPECIALIST)).toBeUndefined();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each([
    ["a server error", async () => new Response("boom", { status: 502 })],
    ["a network failure", async () => Promise.reject(new TypeError("fetch failed"))],
  ])("lets the turn go without the block on %s and tries again next turn", async (_label, failure) => {
    fetchMock.mockImplementationOnce(failure);
    const { handlers, logger } = install();

    expect(await turn(handlers, CHAT)).toBeUndefined();
    expect(logger.warn).toHaveBeenCalledWith(expect.stringContaining("goal brief unavailable"));
    expect(await turn(handlers, CHAT)).toEqual({ prependContext: "<active_goal>A</active_goal>" });
  });

  it("does not count the block as given when the runtime has stopped waiting for the hook", async () => {
    const { handlers } = install();
    const expired = {
      sessionKey: CHAT,
      hookInvocation: {
        assertActive: () => {
          throw new Error("hook invocation no longer active");
        },
      },
    };

    expect(await handlers.before_prompt_build({}, expired)).toBeUndefined();
    expect(await turn(handlers, CHAT)).toEqual({ prependContext: "<active_goal>A</active_goal>" });
  });
});
