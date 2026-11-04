import { randomUUID } from "node:crypto";
import { mkdir, readFile, rename, writeFile } from "node:fs/promises";
import { homedir } from "node:os";
import { dirname, join } from "node:path";

import type { OpenClawPluginApi } from "openclaw/plugin-sdk/core";

import { resolveSellerclawUiAccount, type ScwUiAccount } from "./channel.js";
import { logInfo, logWarn } from "./log.js";
import { getSharedState } from "./shared-state.js";

/**
 * The cloud's copy of the owner's OpenClaw cron jobs, kept from inside the gateway.
 *
 * OpenClaw's scheduler is the truth about recurring tasks while this machine lives; the cloud keeps
 * a durable mirror for its UI and history, and to put the jobs back on a fresh machine — managed
 * machines have no volume, so the cron store (SQLite next to this plugin's state file) is lost on
 * every recreate. This module follows OpenClaw's "Safe external cron projection" pattern
 * (docs/plugins/hooks/lifecycle.md):
 *
 * - `cron_reconciled` adopts the scheduler instance (`ctx.getCron()`) together with the abort
 *   signal that owns it; `cron_changed` only asks one latest-state worker to re-read it.
 * - Every projection is a COMPLETE snapshot (`POST /agent/cron/state`) stamped with a store epoch
 *   (a random id living next to the cron store, so it dies with it) and a sequence number that only
 *   grows; the cloud drops stale ones and never deletes a job because a snapshot lacks it — only
 *   `removed_job_ids` (from `removed` events) or the owner's own delete remove one.
 * - The cloud answers with `restore`: `cron.add` parameters of jobs it knows and this store has
 *   never had. They are added back through the scheduler, paced, and `declarationKey` makes a
 *   repeated add harmless.
 * - A `finished` event is posted as a run (`POST /agent/cron/runs`); after each reconciliation the
 *   agent server backfills recent run history (the scheduler handle has no history read).
 *
 * Writes (restore, cloud commands add / update / remove, standby) go through the handle a plugin
 * SERVICE gets from `ctx.getCron()`: it normalizes input exactly like the gateway's `cron.add` RPC
 * does (`normalizeCronJobCreate` / `normalizeCronJobPatch`). "Run now" is not here — a run started
 * through that handle dies at once, so the agent server sends it over `cron.run`. The scheduler a
 * hook gets is the raw one — it would store an un-normalized job (no `deleteAfterRun` on one-shot
 * jobs, no default stagger) — so it is only ever read here.
 *
 * Standby: when another agent instance took over this owner's session, the agent server puts this
 * plugin on standby. Nothing leaves the machine any more (no snapshot, no run, no restore), and
 * the jobs the cloud mirrors are disabled so two machines never run the same task. Leaving standby
 * starts over: those jobs are dropped and the cloud hands back the owner's current set to a new
 * store epoch. Nothing is reported either until the agent server confirms (on every accepted ping)
 * that this process holds the session.
 *
 * All state lives in a process-wide slot (`shared-state.ts`): OpenClaw re-evaluates plugin modules
 * on every registry pass and replaces the hook registry each time, while `cron_reconciled` fires
 * once per scheduler — a module-level variable would lose the scheduler at the next pass.
 */

/** `declarationKey` prefix of the jobs SellerClaw creates; everything else keyed belongs to others. */
export const SELLERCLAW_DECLARATION_PREFIX = "sellerclaw:job:";
/** Server-side caps of `/agent/cron/state` and `/agent/cron/runs` (requests above are refused). */
export const MAX_SNAPSHOT_JOBS = 200;
const MAX_PENDING_REMOVALS = 500;
/** The gateway limits cron writes to 30 a minute per method; restore stays under it. */
export const RESTORE_ADD_SPACING_MS = 2_100;
/** Freshness net: re-project even when nothing changed, so a missed hint heals by itself. */
export const PERIODIC_REFRESH_MS = 30 * 60_000;
/**
 * An older cloud without these endpoints answers 404/501; asking again soon is pointless. Just
 * under the periodic refresh, so that refresh is the one that asks again.
 */
export const CLOUD_UNSUPPORTED_BACKOFF_MS = 25 * 60_000;
/** A revoked or replaced token (401/403) will not heal within seconds either. */
export const CLOUD_UNAUTHORIZED_BACKOFF_MS = 10 * 60_000;
const RETRY_MIN_MS = 1_000;
const RETRY_MAX_MS = 120_000;
const CLOUD_POST_TIMEOUT_MS = 20_000;
const LOCAL_POST_TIMEOUT_MS = 5_000;
/** How long a service-started gateway waits for `cron_reconciled` before reading on its own. */
const SERVICE_BOOTSTRAP_DELAY_MS = 60_000;
/** Retry delay when restore items arrive before the service's scheduler handle exists. */
const WRITER_RETRY_DELAY_MS = 15_000;
const RUN_POST_ATTEMPTS = 3;
const RUN_POST_RETRY_MS = [2_000, 10_000];
const STATE_FILE_NAME = "sellerclaw-cron-sync.json";

/**
 * Internal fields OpenClaw's own public view strips before a job leaves the gateway
 * (`toPublicCronJob` in `src/cron/public-job.ts`): runtime authority and provenance. The snapshot
 * otherwise carries the job exactly as `cron.list` returns it.
 */
const PRIVATE_JOB_FIELDS = [
  "operatorSource",
  "sourceConversation",
  "skillLibrarySelections",
  "createdActor",
  "toolsAllowProvenance",
  "toolsAllowExecTarget",
  "toolsAllowExecTargetRequirement",
  "runtimeAuthority",
  "runtimeAuthorityRecoveryRequired",
] as const;
const PRIVATE_STATE_FIELDS = [
  "queuedAtMs",
  "runningReceiptId",
  "startupCatchupAtMs",
  "pacedNextRunAtMs",
  "forcePreservedNextRunAtMs",
  "runningScheduleChangeId",
  "failureAlertIncident",
  "lastFailureNotificationId",
] as const;

/** Run fields a `finished` event carries that the cloud reads (`cron.runs` entry names). */
const RUN_EVENT_FIELDS = [
  "jobId",
  "runAtMs",
  "status",
  "completionStatus",
  "summary",
  "error",
  "durationMs",
  "nextRunAtMs",
  "model",
  "provider",
  "usage",
  "runId",
  "sessionId",
  "sessionKey",
  "delivered",
  "deliveryStatus",
  "deliveryError",
] as const;

type JsonRecord = Record<string, unknown>;

/** What the projection reads: the scheduler adopted from `cron_reconciled` (or a fallback). */
export type CronReader = {
  list(options: { includeDisabled: true }): Promise<unknown[]>;
  status?: () => Promise<unknown>;
};

/** The plugin-service scheduler handle (`ctx.getCron()` of `api.registerService`). */
export type CronWriter = {
  list(options: { includeDisabled: true }): Promise<unknown[]>;
  add(input: JsonRecord): Promise<unknown>;
  update(id: string, patch: JsonRecord): Promise<unknown>;
  remove(id: string): Promise<unknown>;
};

/** The host scheduler a plugin service gets (`ctx.scheduler`, `PluginServiceSchedulerV1`). */
type ServiceScheduler = {
  schedule(params: { id: string; delayMs: number; run: () => Promise<unknown> }): unknown;
};

/** Persisted next to the cron store, so it shares the store's lifetime. */
export type CronSyncFile = {
  storeEpoch: string;
  lastSeq: number;
  pendingRemovedIds: string[];
  standby: { since: number; disabledJobIds: string[] } | null;
};

type SyncState = {
  api: OpenClawPluginApi | null;
  reader: CronReader | null;
  readerSource: "reconciled" | "changed-hint" | "service" | null;
  hasBaseline: boolean;
  cronEnabled: boolean;
  reconciliationSignal: AbortSignal | null;
  lifecycle: AbortController;
  requestedRevision: number;
  appliedRevision: number;
  worker: Promise<void>;
  activeAttempt: AbortController | null;
  writerGetter: (() => CronWriter | undefined) | null;
  /** Runs work in the service's own background context (see {@link inServiceBackground}). */
  serviceScheduler: ServiceScheduler | null;
  stateDir: string | null;
  /** OpenClaw's own state dir as a plugin service sees it (`ctx.stateDir`). */
  serviceStateDir: string | null;
  file: CronSyncFile | null;
  fileChain: Promise<unknown>;
  mirroredIds: Set<string>;
  foreignIds: Set<string>;
  /**
   * The agent server confirmed this process holds the owner's session. Until then nothing
   * cron-related leaves it: a laptop waking up after another instance took over learns that only
   * a ping later, and must not report its stale jobs meanwhile.
   */
  confirmedActive: boolean;
  /** Jobs this process removed itself when it came back from standby: not the owner's deletions. */
  resetJobIds: Set<string>;
  restoreQueue: JsonRecord[];
  restoreKeys: Set<string>;
  restoreWorker: Promise<void> | null;
  lastAddAt: number;
  runsChain: Promise<void>;
  needsRunBackfill: boolean;
  cloudBackoffUntil: number;
  refreshTimer: ReturnType<typeof setInterval> | null;
  retryTimer: ReturnType<typeof setTimeout> | null;
  bootstrapTimer: ReturnType<typeof setTimeout> | null;
  loggedOnce: Set<string>;
};

function createState(): SyncState {
  return {
    api: null,
    reader: null,
    readerSource: null,
    hasBaseline: false,
    cronEnabled: true,
    reconciliationSignal: null,
    lifecycle: new AbortController(),
    requestedRevision: 0,
    appliedRevision: 0,
    worker: Promise.resolve(),
    activeAttempt: null,
    writerGetter: null,
    serviceScheduler: null,
    stateDir: null,
    serviceStateDir: null,
    file: null,
    fileChain: Promise.resolve(),
    mirroredIds: new Set(),
    foreignIds: new Set(),
    confirmedActive: false,
    resetJobIds: new Set(),
    restoreQueue: [],
    restoreKeys: new Set(),
    restoreWorker: null,
    lastAddAt: 0,
    runsChain: Promise.resolve(),
    needsRunBackfill: false,
    cloudBackoffUntil: 0,
    refreshTimer: null,
    retryTimer: null,
    bootstrapTimer: null,
    loggedOnce: new Set(),
  };
}

function state(): SyncState {
  return getSharedState("cron-sync:state", createState);
}

// --- small helpers --------------------------------------------------------------------------

function isRecord(value: unknown): value is JsonRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function asString(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function warn(msg: string): void {
  const api = state().api;
  if (api) logWarn(api, msg);
  else console.warn(msg);
}

function info(msg: string): void {
  const api = state().api;
  if (api) logInfo(api, msg);
  else console.info(msg);
}

function warnOnce(key: string, msg: string): void {
  const s = state();
  if (s.loggedOnce.has(key)) return;
  s.loggedOnce.add(key);
  warn(msg);
}

function abortableSleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) {
      reject(signal.reason ?? new Error("aborted"));
      return;
    }
    const timer = setTimeout(() => {
      signal.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    const onAbort = (): void => {
      clearTimeout(timer);
      reject(signal.reason ?? new Error("aborted"));
    };
    signal.addEventListener("abort", onAbort, { once: true });
  });
}

/** A Node timer must not keep the gateway process alive on its own (DOM typings lack `unref`). */
function unrefTimer(timer: unknown): void {
  (timer as { unref?: () => void } | null)?.unref?.();
}

function errorText(err: unknown): string {
  const text = err instanceof Error ? err.message : String(err);
  return text.slice(0, 2_000);
}

function currentAccount(): ScwUiAccount | null {
  const api = state().api;
  if (!api) return null;
  try {
    return resolveSellerclawUiAccount(api.config);
  } catch {
    return null;
  }
}

/**
 * Run `work` from the service's background context, not the caller's.
 *
 * A command arrives on an HTTP route whose caller authority lacks `operator.admin`. Removing a job
 * from there drops it, then fails to clean up its run sessions ("missing scope: operator.admin");
 * the service scheduler dispatches outside any request — the way OpenClaw starts its own
 * background work — and the cleanup goes through. ("Run now" is not here: a run started through
 * the service handle dies at once, so the agent server sends it over the `cron.run` RPC.)
 */
function inServiceBackground<T>(work: () => Promise<T>): Promise<T> {
  const scheduler = state().serviceScheduler;
  if (!scheduler) return work();
  return new Promise<T>((resolve, reject) => {
    try {
      scheduler.schedule({
        id: `cron-command:${randomUUID()}`,
        delayMs: 0,
        run: async () => {
          try {
            resolve(await work());
          } catch (err) {
            reject(err);
          }
        },
      });
    } catch (err) {
      reject(err);
    }
  });
}

function currentWriter(): CronWriter | null {
  const getter = state().writerGetter;
  if (!getter) return null;
  try {
    return getter() ?? null;
  } catch {
    // The service is stopping or its scheduler was replaced; the next start hands a new getter.
    return null;
  }
}

/** A job of a foreign namespace (`heartbeat:*`, `memory-core:*`, …) or a system payload. */
export function isMirroredJob(job: unknown): job is JsonRecord & { id: string } {
  if (!isRecord(job) || !asString(job.id)) return false;
  const payload = isRecord(job.payload) ? job.payload : {};
  if (payload.kind === "heartbeat") return false;
  const key = job.declarationKey;
  if (key === undefined || key === null || key === "") return true;
  return typeof key === "string" && key.startsWith(SELLERCLAW_DECLARATION_PREFIX);
}

/** The job as `cron.list` shows it, minus OpenClaw's private runtime fields; never the live object. */
export function publicCronJob(job: JsonRecord): JsonRecord {
  const copy = JSON.parse(JSON.stringify(job)) as JsonRecord;
  for (const field of PRIVATE_JOB_FIELDS) delete copy[field];
  if (isRecord(copy.state)) {
    for (const field of PRIVATE_STATE_FIELDS) delete copy.state[field];
  }
  return copy;
}

type CloudOutcome = "ok" | "unsupported" | "unauthorized" | "retryable";

function classifyStatus(status: number): CloudOutcome {
  if (status >= 200 && status < 300) return "ok";
  if (status === 404 || status === 405 || status === 501) return "unsupported";
  if (status === 401 || status === 403) return "unauthorized";
  return "retryable";
}

/**
 * One POST to the cloud with the agent's bearer: no retries of its own (callers own the policy)
 * and a hard timeout, so a hung cloud can never wedge the projection worker.
 */
export async function postCloudJson(
  account: ScwUiAccount,
  pathname: string,
  body: unknown,
  signal?: AbortSignal,
): Promise<{ status: number; json: unknown }> {
  const url = `${account.apiBaseUrl.replace(/\/$/, "")}${pathname}`;
  const timeout = AbortSignal.timeout(CLOUD_POST_TIMEOUT_MS);
  const res = await fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${account.agentApiKey}`,
    },
    body: JSON.stringify(body),
    signal: signal ? AbortSignal.any([signal, timeout]) : timeout,
  });
  const text = await res.text().catch(() => "");
  let json: unknown = null;
  if (text) {
    try {
      json = JSON.parse(text);
    } catch {
      json = null;
    }
  }
  return { status: res.status, json };
}

// --- the state file (store epoch, sequence, pending removals, standby) ----------------------

async function resolveStateDir(): Promise<string> {
  const s = state();
  if (s.stateDir) return s.stateDir;
  let dir = "";
  const status = s.reader?.status;
  if (typeof status === "function") {
    try {
      const value = await status.call(s.reader);
      const sqlitePath = isRecord(value) ? asString(value.sqlitePath) || asString(value.storePath) : "";
      if (sqlitePath) dir = dirname(sqlitePath);
    } catch {
      dir = "";
    }
  }
  if (!dir) {
    // The cron store lives in `<OpenClaw state dir>/state/` (`cron.status().sqlitePath`).
    const root =
      s.serviceStateDir || asString(process.env.OPENCLAW_STATE_DIR) || join(homedir(), ".openclaw");
    dir = join(root, "state");
  }
  s.stateDir = dir;
  return dir;
}

function parseSyncFile(raw: unknown): CronSyncFile | null {
  if (!isRecord(raw) || !asString(raw.storeEpoch)) return null;
  const lastSeq = typeof raw.lastSeq === "number" && Number.isFinite(raw.lastSeq) ? raw.lastSeq : 0;
  const removed = Array.isArray(raw.pendingRemovedIds)
    ? raw.pendingRemovedIds.filter((v): v is string => typeof v === "string" && v.length > 0)
    : [];
  let standby: CronSyncFile["standby"] = null;
  if (isRecord(raw.standby)) {
    const ids = Array.isArray(raw.standby.disabledJobIds)
      ? raw.standby.disabledJobIds.filter((v): v is string => typeof v === "string" && v.length > 0)
      : [];
    const since = typeof raw.standby.since === "number" ? raw.standby.since : Date.now();
    standby = { since, disabledJobIds: ids };
  }
  return { storeEpoch: asString(raw.storeEpoch), lastSeq, pendingRemovedIds: removed, standby };
}

function serializeFileOp<T>(op: () => Promise<T>): Promise<T> {
  const s = state();
  const next = s.fileChain.then(op, op);
  s.fileChain = next.catch(() => undefined);
  return next;
}

async function writeSyncFile(file: CronSyncFile): Promise<void> {
  const dir = await resolveStateDir();
  await mkdir(dir, { recursive: true });
  const target = join(dir, STATE_FILE_NAME);
  const tmp = `${target}.${randomUUID()}.tmp`;
  await writeFile(tmp, JSON.stringify(file), { encoding: "utf-8", mode: 0o600 });
  await rename(tmp, target);
}

/** The persisted sync state, created (with a fresh store epoch) the first time it is needed. */
export function loadSyncFile(): Promise<CronSyncFile> {
  return serializeFileOp(async () => {
    const s = state();
    if (s.file) return s.file;
    const dir = await resolveStateDir();
    const target = join(dir, STATE_FILE_NAME);
    let raw: string | null = null;
    try {
      raw = await readFile(target, "utf-8");
    } catch {
      raw = null;
    }
    let parsed: CronSyncFile | null = null;
    if (raw !== null) {
      try {
        parsed = parseSyncFile(JSON.parse(raw));
      } catch {
        parsed = null;
      }
      if (!parsed) warn(`sellerclaw-ui[cron]: ${target} is unreadable; starting a new store epoch`);
    }
    const file = parsed ?? { storeEpoch: randomUUID(), lastSeq: 0, pendingRemovedIds: [], standby: null };
    if (!parsed) await writeSyncFile(file);
    s.file = file;
    return file;
  });
}

function saveSyncFile(): Promise<void> {
  return serializeFileOp(async () => {
    const file = state().file;
    if (file) await writeSyncFile(file);
  });
}

/** Monotonic across restarts within one epoch: never below the last one handed out. */
async function nextSeq(): Promise<number> {
  const file = await loadSyncFile();
  const seq = Math.max(file.lastSeq + 1, Date.now());
  file.lastSeq = seq;
  await saveSyncFile();
  return seq;
}

export function isStandby(): boolean {
  return state().file?.standby != null;
}

async function noteRemovedJobId(id: string): Promise<void> {
  // Recorded before any await when the file is already loaded, so the snapshot this same event
  // requests carries the removal.
  const file = state().file ?? (await loadSyncFile());
  state().mirroredIds.delete(id);
  if (file.pendingRemovedIds.includes(id)) return;
  // Bounded: a cloud that never accepts (pre-v7) must not grow the file without end.
  file.pendingRemovedIds = [...file.pendingRemovedIds, id].slice(-MAX_PENDING_REMOVALS);
  await saveSyncFile();
}

async function dropReportedRemovals(ids: string[]): Promise<void> {
  if (!ids.length) return;
  const file = await loadSyncFile();
  const sent = new Set(ids);
  file.pendingRemovedIds = file.pendingRemovedIds.filter((id) => !sent.has(id));
  await saveSyncFile();
}

// --- the projection worker ------------------------------------------------------------------

const NEVER_ABORTED = new AbortController().signal;

function ensureLifecycle(): void {
  const s = state();
  if (s.lifecycle.signal.aborted) s.lifecycle = new AbortController();
}

function ensureRefreshTimer(): void {
  const s = state();
  if (s.refreshTimer) return;
  s.refreshTimer = setInterval(() => {
    if (state().reader) void requestCronProjection();
  }, PERIODIC_REFRESH_MS);
  unrefTimer(s.refreshTimer);
}

function scheduleRetryProjection(delayMs: number): void {
  const s = state();
  if (s.retryTimer) return;
  s.retryTimer = setTimeout(() => {
    state().retryTimer = null;
    void requestCronProjection();
  }, delayMs);
  unrefTimer(s.retryTimer);
}

/** Ask the single worker to project the latest state; superseded attempts are aborted. */
export function requestCronProjection(): Promise<void> {
  const s = state();
  const target = ++s.requestedRevision;
  s.activeAttempt?.abort();
  s.worker = s.worker
    .then(async () => {
      if (!state().lifecycle.signal.aborted && state().appliedRevision < target) {
        await projectLatest();
      }
    })
    .catch((err: unknown) => {
      warn(`sellerclaw-ui[cron]: projection worker failed: ${errorText(err)}`);
    });
  return s.worker;
}

async function projectLatest(): Promise<void> {
  const s = state();
  let retryMs = RETRY_MIN_MS;
  while (!s.lifecycle.signal.aborted && s.appliedRevision < s.requestedRevision) {
    const ownerSignal = s.reconciliationSignal ?? NEVER_ABORTED;
    const reader = s.reader;
    if (!reader || ownerSignal.aborted) return;
    const lifecycleSignal = s.lifecycle.signal;
    const targetRevision = s.requestedRevision;
    const attempt = new AbortController();
    const signal = AbortSignal.any([lifecycleSignal, ownerSignal, attempt.signal]);
    s.activeAttempt = attempt;
    try {
      await projectOnce(reader, signal);
      if (signal.aborted || targetRevision !== s.requestedRevision) continue;
      s.appliedRevision = targetRevision;
      retryMs = RETRY_MIN_MS;
    } catch (err) {
      if (lifecycleSignal.aborted || ownerSignal.aborted) return;
      if (attempt.signal.aborted) continue;
      warn(`sellerclaw-ui[cron]: snapshot to the cloud failed; retrying in ${retryMs}ms: ${errorText(err)}`);
      try {
        await abortableSleep(retryMs, signal);
      } catch {
        if (lifecycleSignal.aborted || ownerSignal.aborted) return;
        if (attempt.signal.aborted) continue;
      }
      retryMs = Math.min(retryMs * 2, RETRY_MAX_MS);
    } finally {
      if (s.activeAttempt === attempt) s.activeAttempt = null;
    }
  }
}

function rememberJobIds(jobs: unknown[]): JsonRecord[] {
  const s = state();
  const mirrored: JsonRecord[] = [];
  s.mirroredIds.clear();
  s.foreignIds.clear();
  for (const job of jobs) {
    if (!isRecord(job) || !asString(job.id)) continue;
    if (isMirroredJob(job)) {
      mirrored.push(job);
      s.mirroredIds.add(asString(job.id));
    } else {
      s.foreignIds.add(asString(job.id));
    }
  }
  return mirrored;
}

async function projectOnce(reader: CronReader, signal: AbortSignal): Promise<void> {
  const s = state();
  if (!s.confirmedActive) return; // the confirmation projects again
  const file = await loadSyncFile();
  if (file.standby) return; // replaced by another instance: nothing leaves this machine
  if (!s.cronEnabled) {
    warnOnce("cron-disabled", "sellerclaw-ui[cron]: cron is disabled on this agent; not reporting jobs");
    return;
  }
  if (Date.now() < s.cloudBackoffUntil) return;
  const account = currentAccount();
  if (!account) return;

  const jobs = await reader.list({ includeDisabled: true });
  if (signal.aborted) return;
  const mirrored = rememberJobIds(Array.isArray(jobs) ? jobs : []);
  if (mirrored.length > MAX_SNAPSHOT_JOBS) {
    warnOnce("too-many-jobs", `sellerclaw-ui[cron]: ${mirrored.length} jobs; reporting the first ${MAX_SNAPSHOT_JOBS}`);
  }
  const removed = file.pendingRemovedIds.slice(0, MAX_SNAPSHOT_JOBS);
  const seq = await nextSeq();
  const { status, json } = await postCloudJson(
    account,
    "/agent/cron/state",
    {
      store_epoch: file.storeEpoch,
      seq,
      jobs: mirrored.slice(0, MAX_SNAPSHOT_JOBS).map(publicCronJob),
      removed_job_ids: removed,
    },
    signal,
  );
  const outcome = classifyStatus(status);
  if (outcome === "unsupported") {
    // A cloud from before protocol 7: say so once in a while, at the quietest level.
    s.cloudBackoffUntil = Date.now() + CLOUD_UNSUPPORTED_BACKOFF_MS;
    info(`sellerclaw-ui[cron]: the cloud has no /agent/cron/state (HTTP ${status}); asking again later`);
    return;
  }
  if (outcome === "unauthorized") {
    s.cloudBackoffUntil = Date.now() + CLOUD_UNAUTHORIZED_BACKOFF_MS;
    info(`sellerclaw-ui[cron]: the cloud refused the agent token (HTTP ${status}); asking again later`);
    return;
  }
  if (outcome !== "ok") throw new Error(`HTTP ${status}`);
  s.cloudBackoffUntil = 0;
  const accepted = isRecord(json) && json.accepted === true;
  if (!accepted) return; // stale by the cloud's (epoch, seq): the next snapshot supersedes it
  await dropReportedRemovals(removed);
  // A superseded scheduler (reload) or a closing gateway must not act on this answer; a merely
  // newer revision may — its own snapshot follows right after.
  if (s.lifecycle.signal.aborted || s.reconciliationSignal?.aborted || isStandby()) return;
  enqueueRestore(isRecord(json) ? json.restore : undefined);
  void triggerRunBackfill(account);
}

// --- restore ----------------------------------------------------------------------------------

function restoreKey(item: JsonRecord): string {
  return asString(item.declarationKey) || JSON.stringify(item);
}

/** A one-shot whose moment has passed: recreating it would run a stale task right away. */
function isPastOneShot(item: JsonRecord): boolean {
  const schedule = isRecord(item.schedule) ? item.schedule : null;
  if (!schedule || schedule.kind !== "at") return false;
  const at = Date.parse(asString(schedule.at));
  return Number.isFinite(at) && at <= Date.now();
}

function enqueueRestore(items: unknown): void {
  if (!Array.isArray(items) || items.length === 0) return;
  const s = state();
  for (const item of items) {
    if (!isRecord(item)) continue;
    const key = restoreKey(item);
    if (s.restoreKeys.has(key)) continue;
    s.restoreKeys.add(key);
    s.restoreQueue.push(item);
  }
  if (!s.restoreWorker && s.restoreQueue.length) {
    s.restoreWorker = drainRestoreQueue().finally(() => {
      state().restoreWorker = null;
    });
  }
}

function clearRestoreQueue(): void {
  const s = state();
  s.restoreQueue = [];
  s.restoreKeys.clear();
}

async function drainRestoreQueue(): Promise<void> {
  const s = state();
  while (s.restoreQueue.length) {
    const lifecycleSignal = s.lifecycle.signal;
    if (lifecycleSignal.aborted || isStandby()) {
      clearRestoreQueue();
      return;
    }
    const writer = currentWriter();
    if (!writer) {
      // The service starts alongside the gateway; the next snapshot brings the same list back.
      warnOnce("restore-no-writer", "sellerclaw-ui[cron]: scheduler handle not ready; restoring jobs a bit later");
      clearRestoreQueue();
      scheduleRetryProjection(WRITER_RETRY_DELAY_MS);
      return;
    }
    const item = s.restoreQueue.shift() as JsonRecord;
    const key = restoreKey(item);
    try {
      if (isPastOneShot(item)) {
        info(`sellerclaw-ui[cron]: not restoring ${key}: its one-shot moment has passed`);
        continue;
      }
      const wait = s.lastAddAt + RESTORE_ADD_SPACING_MS - Date.now();
      if (wait > 0) await abortableSleep(wait, lifecycleSignal);
      if (isStandby()) continue;
      s.lastAddAt = Date.now();
      await writer.add(item);
      warn(`sellerclaw-ui[cron]: restored job ${key}`);
    } catch (err) {
      if (lifecycleSignal.aborted) {
        clearRestoreQueue();
        return;
      }
      // The cloud keeps returning a job this store lacks, so the next snapshot retries it.
      warn(`sellerclaw-ui[cron]: restoring ${key} failed: ${errorText(err)}`);
    } finally {
      s.restoreKeys.delete(key);
    }
  }
}

// --- runs ---------------------------------------------------------------------------------------

function isMirroredEvent(event: JsonRecord): boolean {
  const s = state();
  const id = asString(event.jobId);
  if (!id) return false;
  if (s.mirroredIds.has(id)) return true;
  if (s.foreignIds.has(id)) return false;
  // Not seen in a snapshot yet (created moments ago): the hook's job view carries no
  // declarationKey, so only the payload kind can rule it out.
  const job = isRecord(event.job) ? event.job : {};
  const payload = isRecord(job.payload) ? job.payload : {};
  return payload.kind !== "heartbeat";
}

/** A `finished` event as a `cron.runs` entry (the shape the cloud keys on `jobId` + `runAtMs`). */
export function runEntryFromEvent(event: JsonRecord): JsonRecord | null {
  if (!asString(event.jobId) || typeof event.runAtMs !== "number") return null;
  const entry: JsonRecord = { action: "finished", ts: Date.now() };
  for (const field of RUN_EVENT_FIELDS) {
    if (event[field] !== undefined) entry[field] = event[field];
  }
  const job = isRecord(event.job) ? event.job : null;
  if (job && typeof job.name === "string") entry.jobName = job.name;
  return entry;
}

function postRuns(entries: JsonRecord[]): Promise<void> {
  const s = state();
  const work = async (): Promise<void> => {
    if (!s.confirmedActive) {
      // Not known to be the active instance yet: the backfill after the first snapshot sends it.
      s.needsRunBackfill = true;
      return;
    }
    for (let attempt = 0; attempt < RUN_POST_ATTEMPTS; attempt++) {
      const file = await loadSyncFile();
      if (file.standby) return;
      const account = currentAccount();
      if (!account) return;
      try {
        const { status } = await postCloudJson(account, "/agent/cron/runs", { runs: entries });
        const outcome = classifyStatus(status);
        if (outcome === "ok") return;
        if (outcome === "unsupported" || outcome === "unauthorized") {
          info(`sellerclaw-ui[cron]: run report not accepted (HTTP ${status}); dropped`);
          return;
        }
      } catch (err) {
        info(`sellerclaw-ui[cron]: run report failed: ${errorText(err)}`);
      }
      const delay = RUN_POST_RETRY_MS[attempt];
      if (delay === undefined) break;
      try {
        await abortableSleep(delay, s.lifecycle.signal);
      } catch {
        break;
      }
    }
    // The run history still has it: the agent server reads it back after the next snapshot.
    s.needsRunBackfill = true;
    warn("sellerclaw-ui[cron]: a run report did not reach the cloud; it will be backfilled");
  };
  s.runsChain = s.runsChain.then(work, work).catch(() => undefined);
  return s.runsChain;
}

/** Ask the agent server to backfill run history (it reads `cron.runs` over RPC); true if taken. */
async function requestRunBackfill(account: ScwUiAccount, jobIds?: string[]): Promise<boolean> {
  try {
    const res = await fetch(
      `${account.localAgentBaseUrl.replace(/\/$/, "")}/internal/openclaw/cron/backfill-runs`,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${account.internalWebhookSecret}`,
        },
        body: JSON.stringify(jobIds ? { job_ids: jobIds } : {}),
        signal: AbortSignal.timeout(LOCAL_POST_TIMEOUT_MS),
      },
    );
    return res.ok;
  } catch {
    return false;
  }
}

/** After a reconciliation or a lost run report: the last runs of every mirrored job. */
async function triggerRunBackfill(account: ScwUiAccount): Promise<void> {
  const s = state();
  if (!s.needsRunBackfill) return;
  s.needsRunBackfill = false;
  if (!(await requestRunBackfill(account))) s.needsRunBackfill = true;
}

/**
 * Right after a run finished: the hook's event carries no token `usage`, the run history does —
 * the agent server re-reads this job's latest runs and the cloud merges them into the same row.
 */
async function completeFinishedRun(jobId: string): Promise<void> {
  if (isStandby() || !state().confirmedActive) return;
  const account = currentAccount();
  if (account) await requestRunBackfill(account, [jobId]);
}

// --- hooks ------------------------------------------------------------------------------------

type HookCtx = { getCron?: () => unknown; abortSignal?: AbortSignal } | undefined;

function asReader(value: unknown): CronReader | null {
  return isRecord(value) && typeof value.list === "function" ? (value as unknown as CronReader) : null;
}

export function onCronReconciled(event: unknown, ctx: HookCtx): void {
  const s = state();
  const enabled = !(isRecord(event) && event.enabled === false);
  const reconciled = asReader(ctx?.getCron?.());
  if (enabled && !reconciled) {
    warn("sellerclaw-ui[cron]: cron reconciliation did not expose a scheduler");
    return;
  }
  s.reader = reconciled;
  s.readerSource = "reconciled";
  s.hasBaseline = true;
  s.cronEnabled = enabled;
  s.reconciliationSignal = ctx?.abortSignal ?? null;
  s.needsRunBackfill = true;
  if (s.bootstrapTimer) {
    clearTimeout(s.bootstrapTimer);
    s.bootstrapTimer = null;
  }
  ensureLifecycle();
  ensureRefreshTimer();
  void requestCronProjection();
}

export function onCronChanged(event: unknown, ctx: HookCtx): void {
  const s = state();
  if (!s.reader && !s.hasBaseline) {
    // No `cron_reconciled` reached us (plugin loaded after it fired). Without a baseline no
    // newer scheduler can be lost by adopting this one; a later reconciliation replaces it.
    const hinted = asReader(ctx?.getCron?.());
    if (hinted) {
      s.reader = hinted;
      s.readerSource = "changed-hint";
      warnOnce("bootstrap-hint", "sellerclaw-ui[cron]: no cron_reconciled seen; reading the scheduler from a change hint");
      ensureLifecycle();
      ensureRefreshTimer();
    }
  }
  if (isRecord(event)) {
    const action = event.action;
    if (action === "removed" && isMirroredEvent(event) && !s.resetJobIds.has(asString(event.jobId))) {
      void noteRemovedJobId(asString(event.jobId)).catch((err: unknown) => {
        warn(`sellerclaw-ui[cron]: could not record a removed job: ${errorText(err)}`);
      });
    } else if (action === "finished" && isMirroredEvent(event)) {
      const entry = runEntryFromEvent(event);
      if (entry) {
        void postRuns([entry]).then(() => {
          if (entry.usage === undefined) return completeFinishedRun(asString(entry.jobId));
          return undefined;
        });
      }
    }
  }
  if (s.reader) void requestCronProjection();
}

export function onGatewayStart(): void {
  const s = state();
  ensureLifecycle();
  // A plugin-only replacement runs gateway_stop/gateway_start but never replays
  // cron_reconciled: resume with the scheduler we hold while its owner signal is still live.
  if (s.reader && !s.reconciliationSignal?.aborted) {
    ensureRefreshTimer();
    void requestCronProjection();
  }
}

export function onGatewayStop(): void {
  const s = state();
  s.lifecycle.abort();
  s.activeAttempt?.abort();
  clearRestoreQueue();
  for (const timer of [s.retryTimer, s.bootstrapTimer]) if (timer) clearTimeout(timer);
  if (s.refreshTimer) clearInterval(s.refreshTimer);
  s.retryTimer = null;
  s.bootstrapTimer = null;
  s.refreshTimer = null;
}

/** Register the cron hooks on this registry pass (called for every pass, like the other hooks). */
export function registerCronSyncHooks(api: OpenClawPluginApi): void {
  if (typeof api.on !== "function") return;
  state().api = api;
  api.on("cron_reconciled", (event: unknown, ctx?: HookCtx): undefined => {
    onCronReconciled(event, ctx);
    return undefined;
  });
  api.on("cron_changed", (event: unknown, ctx?: HookCtx): undefined => {
    onCronChanged(event, ctx);
    return undefined;
  });
  api.on("gateway_start", (): undefined => {
    onGatewayStart();
    return undefined;
  });
  api.on("gateway_stop", (): undefined => {
    onGatewayStop();
    return undefined;
  });
}

type ServiceCtx = { getCron?: () => unknown; stateDir?: unknown; scheduler?: unknown } | undefined;

/**
 * Register the plugin service whose `ctx.getCron()` is the normalized write handle (and the read
 * fallback when no `cron_reconciled` arrives within a minute of the service start).
 */
export function registerCronSyncService(api: OpenClawPluginApi): void {
  state().api = api;
  const register = (api as { registerService?: (service: unknown) => void }).registerService;
  if (typeof register !== "function") {
    warn("sellerclaw-ui[cron]: api.registerService is unavailable; cron restore and commands are off");
    return;
  }
  register.call(api, {
    id: "sellerclaw-cron-sync",
    start: (ctx: ServiceCtx) => {
      const s = state();
      s.serviceStateDir = asString(ctx?.stateDir) || null;
      const scheduler = ctx?.scheduler as Partial<ServiceScheduler> | undefined;
      s.serviceScheduler = typeof scheduler?.schedule === "function" ? (scheduler as ServiceScheduler) : null;
      const getCron = ctx?.getCron;
      s.writerGetter =
        typeof getCron === "function" ? () => (getCron.call(ctx) as CronWriter | undefined) : null;
      if (!s.writerGetter) {
        warn("sellerclaw-ui[cron]: the service got no cron scheduler; cron restore and commands are off");
        return;
      }
      if (s.bootstrapTimer) clearTimeout(s.bootstrapTimer);
      s.bootstrapTimer = setTimeout(() => {
        const st = state();
        st.bootstrapTimer = null;
        if (st.reader || st.hasBaseline || !st.writerGetter) return;
        st.reader = {
          list: async (options) => {
            const writer = currentWriter();
            if (!writer) throw new Error("scheduler handle not available");
            return await writer.list(options);
          },
        };
        st.readerSource = "service";
        warnOnce("bootstrap-service", "sellerclaw-ui[cron]: no cron_reconciled seen; reading jobs through the service handle");
        ensureLifecycle();
        ensureRefreshTimer();
        void requestCronProjection();
      }, SERVICE_BOOTSTRAP_DELAY_MS);
      unrefTimer(s.bootstrapTimer);
    },
    stop: () => {
      const s = state();
      s.writerGetter = null;
      s.serviceScheduler = null;
      if (s.bootstrapTimer) {
        clearTimeout(s.bootstrapTimer);
        s.bootstrapTimer = null;
      }
    },
  });
}

// --- commands from the cloud and standby (driven by the agent server) -------------------------

export type CronCommandResult = {
  ok: boolean;
  error: string | null;
  store_epoch: string | null;
  job: JsonRecord | null;
  run_id: string | null;
};

const FOREIGN_JOB_ERROR = "That job is not a scheduled task (it belongs to the system); it was left alone.";

/** The job a command names, as the scheduler has it (`null` when it does not know the id). */
async function commandTarget(writer: CronWriter, id: string): Promise<JsonRecord | null> {
  const jobs = await writer.list({ includeDisabled: true });
  const job = (Array.isArray(jobs) ? jobs : []).find((j) => isRecord(j) && j.id === id);
  return isRecord(job) ? job : null;
}

/**
 * Remove a job from the service's background context ({@link inServiceBackground}); `true` when it
 * went. OpenClaw can drop the job and then fail on a side step (cleaning up its run sessions needs
 * `operator.admin`): the job is gone, which is what was asked — not an error. A job still there
 * after a failure is one.
 */
async function removeJob(writer: CronWriter, id: string): Promise<boolean> {
  try {
    const remove = writer.remove.bind(writer);
    const outcome = await inServiceBackground(() => remove(id));
    return isRecord(outcome) && outcome.removed === true;
  } catch (err) {
    if (await commandTarget(writer, id)) throw err;
    warn(`sellerclaw-ui[cron]: job ${id} removed with a cleanup error: ${errorText(err)}`);
    return true;
  }
}

async function readBackJob(writer: CronWriter, id: string): Promise<JsonRecord | null> {
  const jobs = await writer.list({ includeDisabled: true });
  const job = (Array.isArray(jobs) ? jobs : []).find((j) => isRecord(j) && j.id === id);
  return isRecord(job) ? publicCronJob(job) : null;
}

function addedJob(result: unknown): JsonRecord | null {
  if (isRecord(result) && isRecord(result.job)) return result.job;
  return isRecord(result) && asString(result.id) ? result : null;
}

/**
 * Run one `cron_command` from the cloud (add / update / remove / run) through the service's
 * scheduler handle. Never throws: every failure becomes `{ok: false, error}` for the cloud.
 */
export async function executeCronCommand(frame: JsonRecord): Promise<CronCommandResult> {
  let storeEpoch: string | null = null;
  const result = (fields: Partial<CronCommandResult>): CronCommandResult => ({
    ok: false,
    error: null,
    store_epoch: storeEpoch,
    job: null,
    run_id: null,
    ...fields,
  });
  try {
    storeEpoch = (await loadSyncFile()).storeEpoch;
  } catch {
    storeEpoch = null;
  }
  if (isStandby()) {
    return result({ error: "This assistant instance was replaced by another one; it no longer runs tasks." });
  }
  const writer = currentWriter();
  if (!writer) {
    return result({ error: "The assistant's scheduler is not ready yet. Try again in a minute." });
  }
  const action = asString(frame.action);
  const jobId = asString(frame.job_id);
  try {
    if (action === "add") {
      if (!isRecord(frame.job)) return result({ error: "add needs job" });
      // The key makes a repeated add a no-op — and a foreign key would overwrite a system job.
      if (!asString(frame.job.declarationKey).startsWith(SELLERCLAW_DECLARATION_PREFIX)) {
        return result({ error: `add needs a declarationKey starting with ${SELLERCLAW_DECLARATION_PREFIX}` });
      }
      const job = addedJob(await writer.add(frame.job));
      void requestCronProjection();
      return result({ ok: true, job: job ? publicCronJob(job) : null });
    }
    if (action === "update") {
      if (!jobId || !isRecord(frame.patch)) return result({ error: "update needs job_id and patch" });
      const target = await commandTarget(writer, jobId);
      if (target && !isMirroredJob(target)) return result({ error: FOREIGN_JOB_ERROR });
      await writer.update(jobId, frame.patch);
      void requestCronProjection();
      // The change is in: a failed read-back only means the cloud learns the job from the snapshot.
      let job: JsonRecord | null = null;
      try {
        job = await readBackJob(writer, jobId);
      } catch (err) {
        warn(`sellerclaw-ui[cron]: could not read back job ${jobId} after an update: ${errorText(err)}`);
      }
      return result({ ok: true, job });
    }
    if (action === "remove") {
      if (!jobId) return result({ error: "remove needs job_id" });
      const target = await commandTarget(writer, jobId);
      if (target && !isMirroredJob(target)) return result({ error: FOREIGN_JOB_ERROR });
      const removed = await removeJob(writer, jobId);
      // Removing a job the scheduler does not know is done already: deletes are idempotent.
      if (removed && state().mirroredIds.has(jobId)) {
        await noteRemovedJobId(jobId);
      }
      void requestCronProjection();
      return result({ ok: true });
    }
    return result({ error: `unknown action: ${action || "(none)"}` });
  } catch (err) {
    return result({ error: errorText(err) });
  }
}

/** True for a job standby turns off: one the cloud mirrors, i.e. one it would restore elsewhere. */
function isStandbyTarget(job: unknown): job is JsonRecord & { id: string } {
  return isMirroredJob(job);
}

export type StandbyResult = { ok: boolean; standby: boolean; jobIds: string[]; error: string | null };

/**
 * Another instance took over: stop reporting, drop pending restores and disable the mirrored
 * jobs (remembering which, to turn exactly those back on). Idempotent — a repeat also catches a
 * job that was enabled in the meantime.
 */
export async function enterCronStandby(): Promise<StandbyResult> {
  const s = state();
  s.confirmedActive = false;
  const file = await loadSyncFile();
  if (!file.standby) {
    file.standby = { since: Date.now(), disabledJobIds: [] };
    await saveSyncFile();
    warn("sellerclaw-ui[cron]: another agent instance took over; standby (jobs off, nothing reported)");
  }
  const standby = file.standby;
  s.activeAttempt?.abort();
  clearRestoreQueue();
  const writer = currentWriter();
  if (!writer) {
    return { ok: false, standby: true, jobIds: [...standby.disabledJobIds], error: "scheduler not ready" };
  }
  const failures: string[] = [];
  const jobs = await writer.list({ includeDisabled: true });
  for (const job of Array.isArray(jobs) ? jobs : []) {
    if (!isStandbyTarget(job) || job.enabled === false) continue;
    try {
      await writer.update(job.id, { enabled: false });
      if (!standby.disabledJobIds.includes(job.id)) standby.disabledJobIds.push(job.id);
    } catch (err) {
      failures.push(`${job.id}: ${errorText(err)}`);
    }
  }
  await saveSyncFile();
  return {
    ok: failures.length === 0,
    standby: true,
    jobIds: [...standby.disabledJobIds],
    error: failures.length ? failures.join("; ") : null,
  };
}

/**
 * The agent server says this instance holds the owner's session — on every accepted ping.
 *
 * Coming back from standby starts over: every job we mirror is dropped and the store takes a new
 * epoch, so the cloud hands back the owner's current set. Tasks deleted, paused or changed on the
 * other instance meanwhile stay that way, and none runs twice; re-enabling the old jobs would
 * revive them. Their run history is kept in the cloud.
 */
export async function leaveCronStandby(): Promise<StandbyResult> {
  const s = state();
  const file = await loadSyncFile();
  if (!file.standby) {
    if (!s.confirmedActive) {
      s.confirmedActive = true;
      if (s.reader) void requestCronProjection();
    }
    return { ok: true, standby: false, jobIds: [], error: null };
  }
  const writer = currentWriter();
  if (!writer) return { ok: false, standby: true, jobIds: [], error: "scheduler not ready" };
  const jobs = await writer.list({ includeDisabled: true });
  const dropped: string[] = [];
  const failures: string[] = [];
  for (const job of Array.isArray(jobs) ? jobs : []) {
    if (!isMirroredJob(job)) continue;
    s.resetJobIds.add(job.id); // before the removal: its hook event may arrive at any moment
    try {
      // Through the service, like the owner's Delete: from this route a job with run sessions
      // would go and then fail its cleanup, holding standby for another ping.
      await removeJob(writer, job.id);
      dropped.push(job.id);
    } catch (err) {
      failures.push(`${job.id}: ${errorText(err)}`);
    }
  }
  if (failures.length) return { ok: false, standby: true, jobIds: dropped, error: failures.join("; ") };
  file.standby = null;
  file.storeEpoch = randomUUID();
  file.pendingRemovedIds = [];
  await saveSyncFile();
  s.mirroredIds.clear();
  s.confirmedActive = true;
  s.needsRunBackfill = true;
  warn("sellerclaw-ui[cron]: this agent instance is active again; its jobs come back from the cloud");
  if (s.reader) void requestCronProjection();
  return { ok: true, standby: false, jobIds: dropped, error: null };
}

/** Test-only: forget everything (timers included). */
export function __resetCronSyncState(): void {
  const s = state();
  onGatewayStop();
  Object.assign(s, createState());
}

/** Test-only: settle the worker, the restore queue and the run reports. */
export async function __drainCronSync(): Promise<void> {
  const s = state();
  for (let i = 0; i < 5; i++) {
    await s.worker;
    await (s.restoreWorker ?? Promise.resolve());
    await s.runsChain;
    await s.fileChain;
  }
}
