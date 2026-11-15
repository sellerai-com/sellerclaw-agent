import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { OpenClawPluginApi } from "openclaw/plugin-sdk/core";

import {
  COMPLETION_TURN_REPORT_DELAY_MS,
  completionTurnChildSessionKey,
  registerCompletionTurnHandinReport,
} from "../completion-turn-handin.js";

const CHAT = "agent:supervisor:sellerclaw-ui:direct:a2d2c50c-feaf-44c4-b9be-abe021c0a74c";
const CHILD = "agent:sellercart:subagent:33ef9a2f-3961-460a-a278-24370216d7e6";
// The run id OpenClaw gave a real completion turn (2026.11.2, Claude subscription brain).
const ANNOUNCE_RUN = `announce:v1:${CHILD}:d75dc533-044d-4924-afdc-c869dbb9d34d`;
const SETTLE_RUN = `announce:requester-settle:supervisor:${CHAT}:584e2a04-c310-45ca-a4b3-c2790d3a9caa`;
const ANNOUNCE_TRIGGER =
  `[Inter-session message] sourceSession=${CHILD} sourceChannel=internal ` +
  "sourceTool=subagent_announce isUser=false\nContinue the OpenClaw runtime event.";

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

type Handler = (event: Record<string, unknown>, ctx?: Record<string, unknown>) => unknown;

function install(config: Record<string, unknown> = CONFIG): {
  handler: Handler;
  logger: { info: ReturnType<typeof vi.fn>; warn: ReturnType<typeof vi.fn>; error: ReturnType<typeof vi.fn> };
} {
  const on = vi.fn();
  const logger = { info: vi.fn(), warn: vi.fn(), error: vi.fn() };
  registerCompletionTurnHandinReport({ config, logger, on } as unknown as OpenClawPluginApi);
  const call = on.mock.calls.find((c) => c[0] === "agent_end");
  if (!call) throw new Error("agent_end was not registered");
  return { handler: call[1] as Handler, logger };
}

const userTurn = (text: string) => [{ role: "user", content: [{ type: "text", text }] }];

describe("completionTurnChildSessionKey", () => {
  it.each([
    ["announce run names the specialist", ANNOUNCE_RUN, undefined, CHILD],
    ["yield continuation names no specialist", SETTLE_RUN, undefined, ""],
    ["ordinary chat turn", "8e7f61a2-1b0c-4d8e-9f3a-5c2b7d6e4a10", userTurn(ANNOUNCE_TRIGGER), ""],
    ["announce prefix without a subagent session", "announce:v1:agent:supervisor:main:run-1", undefined, ""],
    ["no run id: trigger from an announcement", undefined, userTurn(ANNOUNCE_TRIGGER), CHILD],
    [
      "no run id: same words pasted by the owner, no announce source",
      undefined,
      userTurn(`look at sourceSession=${CHILD}`),
      "",
    ],
    ["nothing at all", undefined, undefined, ""],
  ])("%s", (_label, runId, messages, expected) => {
    expect(completionTurnChildSessionKey(runId, messages)).toBe(expected);
  });
});

describe("registerCompletionTurnHandinReport", () => {
  const originalFetch = globalThis.fetch;
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    vi.useFakeTimers();
    fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    globalThis.fetch = fetchMock as unknown as typeof fetch;
  });

  afterEach(() => {
    vi.useRealTimers();
    globalThis.fetch = originalFetch;
  });

  it("waits two pings before reporting, so the cloud sees the session free", async () => {
    const { handler } = install();

    handler({ runId: ANNOUNCE_RUN }, { sessionKey: CHAT });
    await vi.advanceTimersByTimeAsync(COMPLETION_TURN_REPORT_DELAY_MS - 1);
    expect(fetchMock).not.toHaveBeenCalled();

    await vi.advanceTimersByTimeAsync(1);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(COMPLETION_TURN_REPORT_DELAY_MS).toBe(20_000);
  });

  it("reports the end of a completion turn with the specialist it was about", async () => {
    const { handler, logger } = install();

    const result = handler({ runId: ANNOUNCE_RUN }, { sessionKey: CHAT });
    await vi.advanceTimersByTimeAsync(COMPLETION_TURN_REPORT_DELAY_MS);

    expect(result).toBeUndefined();
    await vi.waitFor(() =>
      expect(logger.info).toHaveBeenCalledWith(expect.stringContaining("reported completion turn")),
    );
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("https://api.example/agent/goals/subagent-runs/ended");
    expect(init.method).toBe("POST");
    expect((init.headers as Record<string, string>).Authorization).toBe("Bearer sca");
    expect(JSON.parse(String(init.body))).toEqual({
      childSessionKey: CHILD,
      requesterSessionKey: CHAT,
      runId: ANNOUNCE_RUN,
      trigger: "completion_turn",
    });
  });

  it("takes the run id from the context when the event has none", async () => {
    const { handler } = install();

    handler({}, { sessionKey: CHAT, runId: ANNOUNCE_RUN });
    await vi.advanceTimersByTimeAsync(COMPLETION_TURN_REPORT_DELAY_MS);

    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(JSON.parse(String(init.body))).toMatchObject({ childSessionKey: CHILD, runId: ANNOUNCE_RUN });
  });

  it.each([
    ["an ordinary chat turn", { runId: "turn-1" }, { sessionKey: CHAT }],
    ["a yield continuation", { runId: SETTLE_RUN }, { sessionKey: CHAT }],
    ["a completion turn without its session", { runId: ANNOUNCE_RUN }, {}],
  ])("stays silent for %s", async (_label, event, ctx) => {
    const { handler } = install();

    handler(event, ctx);
    await vi.advanceTimersByTimeAsync(COMPLETION_TURN_REPORT_DELAY_MS);

    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("logs a failed report and never throws into the engine", async () => {
    fetchMock.mockResolvedValue(new Response("nope", { status: 401 }));
    const { handler, logger } = install();

    expect(() => handler({ runId: ANNOUNCE_RUN }, { sessionKey: CHAT })).not.toThrow();
    await vi.advanceTimersByTimeAsync(COMPLETION_TURN_REPORT_DELAY_MS);

    await vi.waitFor(() =>
      expect(logger.error).toHaveBeenCalledWith(expect.stringContaining("completion-turn report failed")),
    );
  });

  it("does not post when the account is not configured", async () => {
    const { handler, logger } = install({});

    handler({ runId: ANNOUNCE_RUN }, { sessionKey: CHAT });
    await vi.advanceTimersByTimeAsync(COMPLETION_TURN_REPORT_DELAY_MS);

    expect(fetchMock).not.toHaveBeenCalled();
    expect(logger.error).toHaveBeenCalledWith(expect.stringContaining("cannot resolve account"));
  });
});
