import type { IncomingMessage, ServerResponse } from "node:http";

import { beforeEach, describe, expect, it, vi } from "vitest";

const { readBodyMock, executeMock, enterMock, leaveMock } = vi.hoisted(() => ({
  readBodyMock: vi.fn(),
  executeMock: vi.fn(),
  enterMock: vi.fn(),
  leaveMock: vi.fn(),
}));

vi.mock("openclaw/plugin-sdk/webhook-ingress", () => ({
  readJsonWebhookBodyOrReject: readBodyMock,
}));

vi.mock("../cron-sync.js", () => ({
  executeCronCommand: (...args: unknown[]) => executeMock(...args),
  enterCronStandby: (...args: unknown[]) => enterMock(...args),
  leaveCronStandby: (...args: unknown[]) => leaveMock(...args),
}));

import {
  CRON_COMMAND_PATH,
  CRON_STANDBY_PATH,
  registerCronCommandRoute,
  registerCronStandbyRoute,
} from "../cron-routes.js";

type HandlerFn = (req: IncomingMessage, res: ServerResponse) => Promise<boolean>;
type Registered = { path: string; auth: string; handler: HandlerFn };

function register(fn: (api: never) => void): Registered {
  const registerHttpRoute = vi.fn();
  fn({
    config: {},
    logger: { info: vi.fn(), warn: vi.fn(), error: vi.fn() },
    registerHttpRoute,
  } as never);
  return registerHttpRoute.mock.calls[0]![0] as Registered;
}

function res(): ServerResponse & { end: ReturnType<typeof vi.fn> } {
  return { statusCode: 0, end: vi.fn() } as unknown as ServerResponse & { end: ReturnType<typeof vi.fn> };
}

const REQ = { headers: {} } as IncomingMessage;

function sent(r: ServerResponse & { end: ReturnType<typeof vi.fn> }): unknown {
  return JSON.parse(String(r.end.mock.calls[0]?.[0]));
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("cron command route", () => {
  it("is a gateway-authenticated route", () => {
    const route = register(registerCronCommandRoute);
    expect(route.path).toBe(CRON_COMMAND_PATH);
    expect(route.auth).toBe("gateway");
  });

  it("answers 200 with the command outcome, a failed one included", async () => {
    const frame = { request_id: "r1", action: "update", job_id: "j1", patch: { enabled: false } };
    const outcome = { ok: false, error: "unknown cron job id: j1", store_epoch: "e", job: null, run_id: null };
    readBodyMock.mockResolvedValue({ ok: true, value: frame });
    executeMock.mockResolvedValue(outcome);
    const r = res();
    await register(registerCronCommandRoute).handler(REQ, r);
    expect(executeMock).toHaveBeenCalledWith(frame);
    expect(r.statusCode).toBe(200);
    expect(sent(r)).toEqual(outcome);
  });

  it.each([
    ["no action", { request_id: "r1" }],
    ["an unknown action", { request_id: "r1", action: "explode" }],
  ])("refuses a frame with %s", async (_name, frame) => {
    readBodyMock.mockResolvedValue({ ok: true, value: frame });
    const r = res();
    await register(registerCronCommandRoute).handler(REQ, r);
    expect(r.statusCode).toBe(400);
    expect(executeMock).not.toHaveBeenCalled();
  });
});

describe("cron standby route", () => {
  it("is a gateway-authenticated route", () => {
    const route = register(registerCronStandbyRoute);
    expect(route.path).toBe(CRON_STANDBY_PATH);
    expect(route.auth).toBe("gateway");
  });

  it.each([
    ["enter", true, 200, { ok: true, standby: true, jobIds: ["a"], error: null }],
    ["leave", false, 200, { ok: true, standby: false, jobIds: ["a"], error: null }],
    ["a pending enter", true, 503, { ok: false, standby: true, jobIds: [], error: "scheduler not ready" }],
  ])("%s", async (_name, standby, status, outcome) => {
    readBodyMock.mockResolvedValue({ ok: true, value: { standby } });
    (standby ? enterMock : leaveMock).mockResolvedValue(outcome);
    const r = res();
    await register(registerCronStandbyRoute).handler(REQ, r);
    expect(r.statusCode).toBe(status);
    expect(sent(r)).toEqual({
      ok: outcome.ok,
      standby: outcome.standby,
      job_ids: outcome.jobIds,
      error: outcome.error,
    });
    expect(standby ? leaveMock : enterMock).not.toHaveBeenCalled();
  });

  it("refuses a body without a boolean standby", async () => {
    readBodyMock.mockResolvedValue({ ok: true, value: { standby: "yes" } });
    const r = res();
    await register(registerCronStandbyRoute).handler(REQ, r);
    expect(r.statusCode).toBe(400);
    expect(enterMock).not.toHaveBeenCalled();
    expect(leaveMock).not.toHaveBeenCalled();
  });
});
