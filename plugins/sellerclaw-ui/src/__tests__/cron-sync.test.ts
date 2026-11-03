import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { OpenClawPluginApi } from "openclaw/plugin-sdk/core";

import {
  __drainCronSync,
  __resetCronSyncState,
  enterCronStandby,
  executeCronCommand,
  isMirroredJob,
  leaveCronStandby,
  publicCronJob,
  registerCronSyncHooks,
  registerCronSyncService,
  RESTORE_ADD_SPACING_MS,
  runEntryFromEvent,
} from "../cron-sync.js";

const CONFIG = {
  channels: {
    "sellerclaw-ui": {
      apiBaseUrl: "https://api.example/",
      userId: "user-1",
      agentApiKey: "sca_test",
      internalWebhookSecret: "hooks-secret",
      localAgentBaseUrl: "http://127.0.0.1:8001",
    },
  },
} as Record<string, unknown>;

type Job = Record<string, unknown> & { id: string };

function makeJob(overrides: Partial<Job> & { id: string }): Job {
  return {
    name: `job ${overrides.id}`,
    description: "",
    enabled: true,
    createdAtMs: 1_700_000_000_000,
    updatedAtMs: 1_700_000_000_000,
    schedule: { kind: "cron", expr: "0 9 * * *", tz: "Europe/Berlin" },
    sessionTarget: "isolated",
    wakeMode: "now",
    payload: { kind: "agentTurn", message: "Summarize orders" },
    delivery: { mode: "none" },
    agentId: "supervisor",
    state: { nextRunAtMs: 1_700_000_100_000 },
    ...overrides,
  };
}

/** A scheduler/service handle over an in-memory job list. */
class FakeScheduler {
  jobs: Job[] = [];
  adds: Array<{ input: Record<string, unknown>; at: number }> = [];
  updates: Array<{ id: string; patch: Record<string, unknown> }> = [];
  removes: string[] = [];
  runs: Array<{ id: string; mode: string }> = [];
  runResult: unknown = { ok: true, enqueued: true, runId: "manual:j1:1" };
  failUpdateFor = new Set<string>();

  constructor(private readonly sqlitePath: string) {}

  async list(): Promise<unknown[]> {
    return this.jobs;
  }

  async status(): Promise<unknown> {
    return { storePath: this.sqlitePath, sqlitePath: this.sqlitePath, enabled: true };
  }

  async add(input: Record<string, unknown>): Promise<unknown> {
    this.adds.push({ input, at: Date.now() });
    const job = makeJob({ ...(input as Partial<Job>), id: `new-${this.adds.length}` });
    this.jobs.push(job);
    return { created: true, job };
  }

  async update(id: string, patch: Record<string, unknown>): Promise<unknown> {
    if (this.failUpdateFor.has(id)) throw new Error("store busy");
    const job = this.jobs.find((j) => j.id === id);
    if (!job) throw new Error(`unknown cron job id: ${id}`);
    this.updates.push({ id, patch });
    Object.assign(job, patch);
    return job;
  }

  async remove(id: string): Promise<unknown> {
    this.removes.push(id);
    const before = this.jobs.length;
    this.jobs = this.jobs.filter((j) => j.id !== id);
    return { ok: true, removed: this.jobs.length !== before };
  }

  async enqueueRun(id: string, mode: string): Promise<unknown> {
    this.runs.push({ id, mode });
    return this.runResult;
  }
}

type Handler = (event: unknown, ctx?: unknown) => unknown;

let dir: string;
let scheduler: FakeScheduler;
let fetchMock: ReturnType<typeof vi.fn>;
let handlers: Map<string, Handler>;
let cloudResponses: Array<{ status: number; body?: unknown }>;
const originalFetch = globalThis.fetch;
const originalStateDir = process.env.OPENCLAW_STATE_DIR;

function api(): OpenClawPluginApi {
  return {
    config: CONFIG,
    logger: { info: vi.fn(), warn: vi.fn(), error: vi.fn() },
    on: (name: string, handler: Handler) => handlers.set(name, handler),
    registerHttpRoute: vi.fn(),
    registerService: (service: { start: (ctx: unknown) => void }) => {
      service.start({ getCron: () => scheduler });
    },
  } as unknown as OpenClawPluginApi;
}

function calls(pathSuffix: string): Array<Record<string, unknown>> {
  return fetchMock.mock.calls
    .filter((c) => String(c[0]).endsWith(pathSuffix))
    .map((c) => JSON.parse(String((c[1] as RequestInit).body)) as Record<string, unknown>);
}

function stateFile(): Record<string, unknown> {
  return JSON.parse(readFileSync(join(dir, "state", "sellerclaw-cron-sync.json"), "utf-8")) as Record<string, unknown>;
}

async function reconcile(enabled = true, abortSignal?: AbortSignal): Promise<void> {
  handlers.get("cron_reconciled")?.({ reason: "startup", enabled }, { getCron: () => scheduler, abortSignal });
  await __drainCronSync();
}

beforeEach(() => {
  __resetCronSyncState();
  dir = mkdtempSync(join(tmpdir(), "cron-sync-"));
  // Paths resolved without a scheduler fall back to OPENCLAW_STATE_DIR: never the real home.
  process.env.OPENCLAW_STATE_DIR = dir;
  // OpenClaw keeps the cron store at <state dir>/state/openclaw.sqlite; the sync file sits beside it.
  scheduler = new FakeScheduler(join(dir, "state", "openclaw.sqlite"));
  handlers = new Map();
  cloudResponses = [];
  fetchMock = vi.fn(async (url: string) => {
    if (String(url).endsWith("/agent/cron/state")) {
      const next = cloudResponses.shift() ?? { status: 200, body: { accepted: true, restore: [] } };
      return new Response(next.body === undefined ? null : JSON.stringify(next.body), { status: next.status });
    }
    if (String(url).endsWith("/agent/cron/runs")) return new Response(null, { status: 204 });
    return new Response(null, { status: 202 });
  });
  globalThis.fetch = fetchMock as unknown as typeof fetch;
  const a = api();
  registerCronSyncHooks(a);
  registerCronSyncService(a);
});

afterEach(async () => {
  await __drainCronSync();
  __resetCronSyncState();
  globalThis.fetch = originalFetch;
  if (originalStateDir === undefined) delete process.env.OPENCLAW_STATE_DIR;
  else process.env.OPENCLAW_STATE_DIR = originalStateDir;
  rmSync(dir, { recursive: true, force: true });
});

describe("isMirroredJob", () => {
  it.each([
    ["no declarationKey", { id: "a" }, true],
    ["our namespace", { id: "a", declarationKey: "sellerclaw:job:123" }, true],
    ["heartbeat namespace", { id: "a", declarationKey: "heartbeat:supervisor", payload: { kind: "heartbeat" } }, false],
    ["memory-core namespace", { id: "a", declarationKey: "memory-core:memory-dreaming-promotion" }, false],
    ["heartbeat payload without key", { id: "a", payload: { kind: "heartbeat" } }, false],
    ["no id", { declarationKey: "sellerclaw:job:1" }, false],
    ["not an object", "job", false],
  ])("%s", (_name, job, expected) => {
    expect(isMirroredJob(job)).toBe(expected);
  });
});

describe("publicCronJob", () => {
  it("drops OpenClaw's private runtime fields and leaves the live job untouched", () => {
    const live = makeJob({
      id: "j1",
      runtimeAuthority: { kind: "x" },
      createdActor: { id: "op" },
      state: { nextRunAtMs: 5, runningReceiptId: "r", lastRunStatus: "ok" },
    });
    const view = publicCronJob(live);
    expect(view.runtimeAuthority).toBeUndefined();
    expect(view.createdActor).toBeUndefined();
    expect(view.state).toEqual({ nextRunAtMs: 5, lastRunStatus: "ok" });
    expect(view.schedule).toEqual({ kind: "cron", expr: "0 9 * * *", tz: "Europe/Berlin" });
    expect((live.state as Record<string, unknown>).runningReceiptId).toBe("r");
    expect(live.runtimeAuthority).toEqual({ kind: "x" });
  });
});

describe("snapshot projection", () => {
  it("posts every mirrored job with a persisted store epoch and a growing seq", async () => {
    scheduler.jobs = [
      makeJob({ id: "ours", declarationKey: "sellerclaw:job:1" }),
      makeJob({ id: "legacy" }),
      makeJob({ id: "hb", declarationKey: "heartbeat:supervisor", payload: { kind: "heartbeat" } }),
      makeJob({ id: "dream", declarationKey: "memory-core:memory-dreaming-promotion" }),
    ];
    await reconcile();

    const [first] = calls("/agent/cron/state");
    expect(first?.jobs).toEqual([
      publicCronJob(scheduler.jobs[0] as Job),
      publicCronJob(scheduler.jobs[1] as Job),
    ]);
    expect(first?.removed_job_ids).toEqual([]);
    const epoch = stateFile().storeEpoch;
    expect(typeof epoch).toBe("string");
    expect(first?.store_epoch).toBe(epoch);
    expect(first?.seq).toBe(stateFile().lastSeq);
    expect(fetchMock.mock.calls[0]?.[1]).toMatchObject({
      method: "POST",
      headers: { Authorization: "Bearer sca_test" },
    });

    handlers.get("cron_changed")?.({ action: "updated", jobId: "ours" }, {});
    await __drainCronSync();
    const second = calls("/agent/cron/state")[1];
    expect(second?.store_epoch).toBe(epoch);
    expect(Number(second?.seq)).toBeGreaterThan(Number(first?.seq));
  });

  it("keeps the epoch and never reuses a seq after a gateway restart", async () => {
    mkdirSync(join(dir, "state"), { recursive: true });
    writeFileSync(
      join(dir, "state", "sellerclaw-cron-sync.json"),
      JSON.stringify({ storeEpoch: "epoch-a", lastSeq: Date.now() + 60_000, pendingRemovedIds: [], standby: null }),
    );
    await reconcile();
    const [snapshot] = calls("/agent/cron/state");
    expect(snapshot?.store_epoch).toBe("epoch-a");
    expect(Number(snapshot?.seq)).toBeGreaterThan(Date.now() + 59_000);
  });

  it("reports removed jobs until the cloud accepts a snapshot carrying them", async () => {
    scheduler.jobs = [makeJob({ id: "gone", declarationKey: "sellerclaw:job:9" })];
    await reconcile();
    scheduler.jobs = [];
    cloudResponses.push({ status: 200, body: { accepted: false, restore: [] } });
    handlers.get("cron_changed")?.({ action: "removed", jobId: "gone", job: { id: "gone" } }, {});
    await __drainCronSync();
    expect(calls("/agent/cron/state")[1]?.removed_job_ids).toEqual(["gone"]);
    expect(stateFile().pendingRemovedIds).toEqual(["gone"]);

    handlers.get("cron_changed")?.({ action: "updated", jobId: "x" }, {});
    await __drainCronSync();
    expect(calls("/agent/cron/state")[2]?.removed_job_ids).toEqual(["gone"]);
    expect(stateFile().pendingRemovedIds).toEqual([]);
  });

  it("never reports the removal of a foreign job", async () => {
    scheduler.jobs = [makeJob({ id: "dream", declarationKey: "memory-core:x" })];
    await reconcile();
    handlers.get("cron_changed")?.({ action: "removed", jobId: "dream" }, {});
    await __drainCronSync();
    expect(calls("/agent/cron/state").at(-1)?.removed_job_ids).toEqual([]);
  });

  it("backs off on a cloud without the endpoint instead of retrying", async () => {
    cloudResponses.push({ status: 404, body: { detail: "Not Found" } });
    await reconcile();
    handlers.get("cron_changed")?.({ action: "updated", jobId: "x" }, {});
    await __drainCronSync();
    expect(calls("/agent/cron/state")).toHaveLength(1);
  });

  it("does not report anything while cron is disabled", async () => {
    await reconcile(false);
    expect(calls("/agent/cron/state")).toHaveLength(0);
  });

  it("drops the answer of a snapshot whose scheduler was superseded", async () => {
    const owner = new AbortController();
    fetchMock.mockImplementationOnce(async () => {
      owner.abort();
      return new Response(JSON.stringify({ accepted: true, restore: [{ declarationKey: "sellerclaw:job:1" }] }), {
        status: 200,
      });
    });
    await reconcile(true, owner.signal);
    expect(scheduler.adds).toHaveLength(0);
  });

  it("reads the scheduler from a change hint when no reconciliation was seen", async () => {
    scheduler.jobs = [makeJob({ id: "ours", declarationKey: "sellerclaw:job:1" })];
    handlers.get("cron_changed")?.({ action: "updated", jobId: "ours" }, { getCron: () => scheduler });
    await __drainCronSync();
    expect(calls("/agent/cron/state")[0]?.jobs).toHaveLength(1);
  });
});

describe("restore", () => {
  it("adds every restore item through the service handle, paced", async () => {
    const restore = [
      { name: "a", declarationKey: "sellerclaw:job:a", schedule: { kind: "cron", expr: "0 9 * * *" }, enabled: true },
      { name: "b", declarationKey: "sellerclaw:job:b", schedule: { kind: "every", everyMs: 3_600_000 }, enabled: false },
    ];
    cloudResponses.push({ status: 200, body: { accepted: true, restore } });
    await reconcile();

    expect(scheduler.adds.map((a) => a.input)).toEqual(restore);
    const gap = (scheduler.adds[1]?.at ?? 0) - (scheduler.adds[0]?.at ?? 0);
    expect(gap).toBeGreaterThanOrEqual(RESTORE_ADD_SPACING_MS - 5);
  }, 10_000);

  it("skips a one-shot whose moment has passed", async () => {
    cloudResponses.push({
      status: 200,
      body: {
        accepted: true,
        restore: [{ name: "late", declarationKey: "sellerclaw:job:late", schedule: { kind: "at", at: "2020-01-01T00:00:00Z" } }],
      },
    });
    await reconcile();
    expect(scheduler.adds).toEqual([]);
  });

  it("ignores restore in a snapshot the cloud did not accept", async () => {
    cloudResponses.push({ status: 200, body: { accepted: false, restore: [{ declarationKey: "sellerclaw:job:a" }] } });
    await reconcile();
    expect(scheduler.adds).toEqual([]);
  });
});

describe("runs", () => {
  it("posts a finished run of a mirrored job as a cron.runs entry", async () => {
    scheduler.jobs = [makeJob({ id: "ours", declarationKey: "sellerclaw:job:1" })];
    await reconcile();
    handlers.get("cron_changed")?.(
      {
        action: "finished",
        jobId: "ours",
        job: { id: "ours", name: "Daily report", payload: { kind: "agentTurn" } },
        runAtMs: 1_700_000_000_123,
        status: "ok",
        summary: "All good",
        durationMs: 4200,
        model: "m",
        provider: "litellm",
        sessionKey: "agent:supervisor:cron:ours:run:abc",
        sessionTarget: "isolated",
      },
      {},
    );
    await __drainCronSync();
    await vi.waitFor(() => expect(calls("/internal/openclaw/cron/backfill-runs").at(-1)).toEqual({ job_ids: ["ours"] }));
    const [body] = calls("/agent/cron/runs");
    expect(body?.runs).toEqual([
      expect.objectContaining({
        action: "finished",
        jobId: "ours",
        jobName: "Daily report",
        runAtMs: 1_700_000_000_123,
        status: "ok",
        summary: "All good",
        durationMs: 4200,
        model: "m",
        provider: "litellm",
        sessionKey: "agent:supervisor:cron:ours:run:abc",
      }),
    ]);
  });

  it.each([
    ["a foreign job", { action: "finished", jobId: "dream", runAtMs: 1, status: "ok" }],
    ["a heartbeat", { action: "finished", jobId: "hb2", runAtMs: 1, status: "ok", job: { payload: { kind: "heartbeat" } } }],
    ["no runAtMs", { action: "finished", jobId: "ours", status: "ok" }],
  ])("does not post %s", async (_name, event) => {
    scheduler.jobs = [
      makeJob({ id: "ours", declarationKey: "sellerclaw:job:1" }),
      makeJob({ id: "dream", declarationKey: "memory-core:x" }),
    ];
    await reconcile();
    handlers.get("cron_changed")?.(event, {});
    await __drainCronSync();
    expect(calls("/agent/cron/runs")).toEqual([]);
  });

  it("asks the agent server to backfill run history after the first accepted snapshot", async () => {
    await reconcile();
    const backfill = fetchMock.mock.calls.filter((c) => String(c[0]).endsWith("/internal/openclaw/cron/backfill-runs"));
    expect(backfill).toHaveLength(1);
    expect(JSON.parse(String((backfill[0]?.[1] as RequestInit).body))).toEqual({});
    expect(String(backfill[0]?.[0])).toBe("http://127.0.0.1:8001/internal/openclaw/cron/backfill-runs");
    expect((backfill[0]?.[1] as RequestInit).headers).toMatchObject({ Authorization: "Bearer hooks-secret" });
  });

  it("maps only the run fields of an event", () => {
    expect(runEntryFromEvent({ action: "finished", jobId: "j", runAtMs: 5, status: "error", error: "boom", job: { name: "N" } }))
      .toEqual({ action: "finished", ts: expect.any(Number), jobId: "j", runAtMs: 5, status: "error", error: "boom", jobName: "N" });
    expect(runEntryFromEvent({ jobId: "j" })).toBeNull();
  });
});

describe("standby", () => {
  it("disables the mirrored jobs, stops reporting and restores exactly those on leave", async () => {
    scheduler.jobs = [
      makeJob({ id: "ours", declarationKey: "sellerclaw:job:1" }),
      makeJob({ id: "legacy" }),
      makeJob({ id: "paused", declarationKey: "sellerclaw:job:2", enabled: false }),
      makeJob({ id: "dream", declarationKey: "memory-core:x" }),
    ];
    await reconcile();
    const postedBefore = calls("/agent/cron/state").length;
    const backfillsBefore = calls("/internal/openclaw/cron/backfill-runs").length;

    const entered = await enterCronStandby();
    expect(entered).toEqual({ ok: true, standby: true, jobIds: ["ours", "legacy"], error: null });
    expect(scheduler.jobs.map((j) => [j.id, j.enabled])).toEqual([
      ["ours", false],
      ["legacy", false],
      ["paused", false],
      ["dream", true],
    ]);
    expect(stateFile().standby).toMatchObject({ disabledJobIds: ["ours", "legacy"] });

    handlers.get("cron_changed")?.({ action: "updated", jobId: "ours" }, {});
    handlers.get("cron_changed")?.(
      { action: "finished", jobId: "ours", runAtMs: 9, status: "ok" },
      {},
    );
    await __drainCronSync();
    expect(calls("/agent/cron/state")).toHaveLength(postedBefore);
    expect(calls("/agent/cron/runs")).toEqual([]);
    expect(calls("/internal/openclaw/cron/backfill-runs")).toHaveLength(backfillsBefore);
    expect(scheduler.adds).toEqual([]);
    const refused = await executeCronCommand({ request_id: "r1", action: "run", job_id: "ours" });
    expect(refused.ok).toBe(false);
    expect(refused.error).toMatch(/replaced/);
    expect(scheduler.runs).toEqual([]);

    const left = await leaveCronStandby();
    await __drainCronSync();
    expect(left).toEqual({ ok: true, standby: false, jobIds: ["ours", "legacy"], error: null });
    expect(scheduler.jobs.map((j) => [j.id, j.enabled])).toEqual([
      ["ours", true],
      ["legacy", true],
      ["paused", false],
      ["dream", true],
    ]);
    expect(stateFile().standby).toBeNull();
    expect(calls("/agent/cron/state").length).toBeGreaterThan(postedBefore);
  });

  it("stays on standby until every job it disabled is back on", async () => {
    scheduler.jobs = [makeJob({ id: "a" }), makeJob({ id: "b" })];
    await reconcile();
    await enterCronStandby();
    scheduler.failUpdateFor.add("b");
    const left = await leaveCronStandby();
    expect(left).toMatchObject({ ok: false, standby: true, jobIds: ["b"] });
    expect(stateFile().standby).toMatchObject({ disabledJobIds: ["b"] });
    scheduler.failUpdateFor.clear();
    expect(await leaveCronStandby()).toMatchObject({ ok: true, standby: false, jobIds: ["b"] });
  });

  it("is a no-op to leave a standby never entered", async () => {
    expect(await leaveCronStandby()).toEqual({ ok: true, standby: false, jobIds: [], error: null });
  });
});

describe("executeCronCommand", () => {
  beforeEach(async () => {
    scheduler.jobs = [makeJob({ id: "j1", declarationKey: "sellerclaw:job:1" })];
    await reconcile();
  });

  it("adds a job and answers with it as the scheduler stored it", async () => {
    const job = { name: "New", declarationKey: "sellerclaw:job:new", schedule: { kind: "cron", expr: "0 8 * * *" } };
    const result = await executeCronCommand({ request_id: "r", action: "add", job });
    expect(scheduler.adds.map((a) => a.input)).toEqual([job]);
    expect(result).toMatchObject({ ok: true, error: null, run_id: null, store_epoch: stateFile().storeEpoch });
    expect(result.job).toMatchObject({ id: "new-1", name: "New", declarationKey: "sellerclaw:job:new" });
  });

  it("updates a job and reads it back", async () => {
    const result = await executeCronCommand({ request_id: "r", action: "update", job_id: "j1", patch: { enabled: false } });
    expect(scheduler.updates).toEqual([{ id: "j1", patch: { enabled: false } }]);
    expect(result).toMatchObject({ ok: true, job: { id: "j1", enabled: false } });
  });

  it("reports an update of an unknown job as a failure", async () => {
    const result = await executeCronCommand({ request_id: "r", action: "update", job_id: "nope", patch: { enabled: true } });
    expect(result).toMatchObject({ ok: false, error: "unknown cron job id: nope", job: null });
  });

  it.each([
    ["a known job", "j1"],
    ["a job the scheduler does not know", "nope"],
  ])("removes %s idempotently", async (_name, id) => {
    const result = await executeCronCommand({ request_id: "r", action: "remove", job_id: id });
    expect(result).toMatchObject({ ok: true, error: null });
    expect(scheduler.removes).toEqual([id]);
  });

  it("starts a forced run and returns its id", async () => {
    const result = await executeCronCommand({ request_id: "r", action: "run", job_id: "j1" });
    expect(scheduler.runs).toEqual([{ id: "j1", mode: "force" }]);
    expect(result).toMatchObject({ ok: true, run_id: "manual:j1:1" });
  });

  it("reports a run that did not start", async () => {
    scheduler.runResult = { ok: true, ran: false, reason: "already-running" };
    const result = await executeCronCommand({ request_id: "r", action: "run", job_id: "j1" });
    expect(result).toMatchObject({ ok: false, error: "The job did not start: already-running.", run_id: null });
  });

  it.each([
    ["add without job", { action: "add" }, "add needs job"],
    ["update without patch", { action: "update", job_id: "j1" }, "update needs job_id and patch"],
    ["remove without id", { action: "remove" }, "remove needs job_id"],
    ["unknown action", { action: "explode" }, "unknown action: explode"],
  ])("refuses %s", async (_name, frame, error) => {
    expect(await executeCronCommand({ request_id: "r", ...frame })).toMatchObject({ ok: false, error });
  });

  it("keeps the store epoch beside the cron store even before any reconciliation", async () => {
    __resetCronSyncState();
    const serviceDir = mkdtempSync(join(tmpdir(), "cron-sync-service-"));
    try {
      registerCronSyncService({
        config: CONFIG,
        logger: { info: vi.fn(), warn: vi.fn(), error: vi.fn() },
        registerService: (service: { start: (ctx: unknown) => void }) =>
          service.start({ getCron: () => scheduler, stateDir: serviceDir }),
      } as unknown as OpenClawPluginApi);
      const result = await executeCronCommand({ request_id: "r", action: "run", job_id: "j1" });
      const file = JSON.parse(readFileSync(join(serviceDir, "state", "sellerclaw-cron-sync.json"), "utf-8"));
      expect(result.store_epoch).toBe(file.storeEpoch);
    } finally {
      rmSync(serviceDir, { recursive: true, force: true });
    }
  });

  it("refuses while the service has no scheduler handle", async () => {
    __resetCronSyncState();
    const result = await executeCronCommand({ request_id: "r", action: "run", job_id: "j1" });
    expect(result.ok).toBe(false);
    expect(result.error).toMatch(/not ready/);
  });
});
