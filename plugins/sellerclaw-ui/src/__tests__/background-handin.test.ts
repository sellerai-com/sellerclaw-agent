import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { OpenClawPluginApi } from "openclaw/plugin-sdk/core";

import { isBackgroundRunSessionKey, registerBackgroundHandinRelay } from "../background-handin.js";

const CRON_RUN = "agent:supervisor:cron:ca82691c-1f62-4348-a866-613cad18786c:run:5f83a645-6608";
const CHAT = "agent:supervisor:sellerclaw-ui:direct:4df02075-3ac1-4f53-a86e-e3bf7775fa9e";
const CHILD = "agent:supplier:subagent:ab91e3dd-926b-4a87-a20b-b81882f9cc13";

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
  registerBackgroundHandinRelay({ config, logger, on } as unknown as OpenClawPluginApi);
  const call = on.mock.calls.find((c) => c[0] === "subagent_ended");
  if (!call) throw new Error("subagent_ended was not registered");
  return { handler: call[1] as Handler, logger };
}

describe("isBackgroundRunSessionKey", () => {
  it.each([
    [CRON_RUN, true],
    ["agent:supervisor:cron:job-1", false],
    [CHAT, false],
    [CHILD, false],
    ["", false],
  ])("%s -> %s", (key, expected) => {
    expect(isBackgroundRunSessionKey(key)).toBe(expected);
  });
});

describe("registerBackgroundHandinRelay", () => {
  const originalFetch = globalThis.fetch;
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    globalThis.fetch = fetchMock as unknown as typeof fetch;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  it("reports a specialist whose spawning run was a background run", async () => {
    const { handler, logger } = install();

    const result = handler({ targetSessionKey: CHILD, runId: "run-7" }, { requesterSessionKey: CRON_RUN });

    expect(result).toBeUndefined();
    await vi.waitFor(() => expect(logger.info).toHaveBeenCalledWith(expect.stringContaining("reported ended run")));
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("https://api.example/agent/goals/subagent-runs/ended");
    expect(init.method).toBe("POST");
    expect((init.headers as Record<string, string>).Authorization).toBe("Bearer sca");
    expect(JSON.parse(String(init.body))).toEqual({
      childSessionKey: CHILD,
      requesterSessionKey: CRON_RUN,
      runId: "run-7",
    });
  });

  it("falls back to the context for the child session", async () => {
    const { handler } = install();

    handler({}, { requesterSessionKey: CRON_RUN, childSessionKey: CHILD });

    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(JSON.parse(String(init.body))).toEqual({
      childSessionKey: CHILD,
      requesterSessionKey: CRON_RUN,
    });
  });

  it.each([
    ["a chat", { targetSessionKey: CHILD }, { requesterSessionKey: CHAT }],
    ["no requester", { targetSessionKey: CHILD }, {}],
    ["no child", {}, { requesterSessionKey: CRON_RUN }],
  ])("stays silent for %s", async (_label, event, ctx) => {
    const { handler } = install();

    handler(event, ctx);
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("logs a failed report and never throws into the engine", async () => {
    fetchMock.mockResolvedValue(new Response("nope", { status: 401 }));
    const { handler, logger } = install();

    expect(() => handler({ targetSessionKey: CHILD }, { requesterSessionKey: CRON_RUN })).not.toThrow();

    await vi.waitFor(() =>
      expect(logger.error).toHaveBeenCalledWith(expect.stringContaining("background hand-in report failed")),
    );
  });

  it("does not post when the account is not configured", async () => {
    const { handler, logger } = install({});

    handler({ targetSessionKey: CHILD }, { requesterSessionKey: CRON_RUN });
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(fetchMock).not.toHaveBeenCalled();
    expect(logger.error).toHaveBeenCalledWith(expect.stringContaining("cannot resolve account"));
  });
});
