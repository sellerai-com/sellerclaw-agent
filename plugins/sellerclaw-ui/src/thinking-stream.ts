import type { AgentEventPayload, OpenClawPluginApi } from "openclaw/plugin-sdk/core";

import { extractTargetFromSessionKey, resolveSellerclawUiAccount } from "./channel.js";
import { logError, logInfo, logWarn } from "./log.js";
import { postThought, postTurnEnd, postTurnStart } from "./send.js";
import type { ScwUiAccount } from "./send.js";
import { getSharedState } from "./shared-state.js";
import { lookupSubagentOrigin } from "./subagent-origins.js";

/**
 * Reports a specialist's thinking WHILE it works, instead of once its run is over.
 *
 * The gap this closes: when the supervisor delegates, it yields and goes quiet, and the
 * specialists that pick the work up run in sessions of their own. Nothing our channel dispatches
 * is running any more, so no reasoning callback reaches us, and the owner watches an empty Thought
 * panel for however long the work takes — minutes, on real tasks. `reasoning-relay.ts` reads the
 * same thinking out of the finished run's transcript at `agent_end`, which is correct but late by
 * construction: it can only speak after the run it describes has ended.
 *
 * An agent-event subscription has neither limitation. It is handed every run in the gateway
 * process — announce/settle runs and subagents included — and handed it as it happens, with the
 * same growing-cumulative shape the dispatch callback uses (see the reasoning buffer in
 * `inbound.ts`, whose flush threshold this mirrors).
 *
 * Verified end to end on a live gateway (03.09.2026): a spawned specialist's run emits `thinking`
 * events carrying `{text, delta}`, this module resolves the chat from the spawn, posts the blocks
 * and closes the run's turn, and the relay stays silent for that run. `agents.defaults` ships
 * `reasoningDefault: "stream"`, which is enough — no caller callback is needed for the events to
 * be emitted, unlike the reasoning callback path our own dispatch uses.
 *
 * What decides whether there is anything to report is the SPECIALIST'S MODEL. On the same stand a
 * specialist on the cheap alias produced no reasoning at all — no `thinking` events, and nothing
 * in its transcript for the relay to find either. Since the owner's effort level is what re-points
 * those aliases, a low effort means an empty panel for reasons that have nothing to do with this
 * module. Do not read silence here as a bug before checking which model the specialist ran on.
 *
 * Scoped deliberately to specialist runs. A live chat turn already streams its own reasoning
 * through the dispatch, and a completion run's thinking arrives together with the answer it
 * explains and must first be judged on its transcript — a run whose whole answer is the silent
 * token contributes nothing the owner reads, and that judgement is only possible once it has
 * finished. Both of those stay with the relay.
 *
 * The cost of speaking early is that the same judgement cannot be made about a specialist: a
 * specialist that ends up declining to answer will have had its thinking shown anyway. Accepted
 * knowingly — a specialist is spawned to do a piece of work, not to decide whether to reply, so
 * the silent token is a completion-run shape, and an empty panel for minutes is the worse of the
 * two failures.
 *
 * Addresses come from `subagent-origins.ts`, exactly as in the relay: a child's session key
 * encodes no chat, and the spawn is the one moment the runtime states the connection.
 */

/** Flush an item once this much has accumulated, mirroring the inbound reasoning buffer. */
const FLUSH_AT = 2000;

/** How long an unfinished run's bookkeeping is kept before it is treated as abandoned. */
const RUN_TTL_MS = 2 * 60 * 60 * 1000;

/**
 * Bound on the bookkeeping maps, whose entries are a key and a timestamp.
 *
 * Generous on purpose. Evicting a `streamedRuns` mark early is the one eviction with teeth: the
 * relay would stop deferring and report the run from its transcript, i.e. say it a second time.
 * The mark is only needed for as long as `agent_end` can still arrive, which is seconds.
 */
const MAX_MARKS = 1000;

/**
 * Bound on runs being streamed right now. Separate and larger because eviction here loses a
 * buffer nobody will report again — the run is already marked as ours — so it must not happen
 * for any reason short of a genuine leak. A live run holds at most one flush worth of text.
 */
const MAX_LIVE_RUNS = 500;

/** How often the maps are swept. Thinking events arrive per model round, sweeping need not. */
const SWEEP_EVERY_MS = 30_000;

interface LiveRun {
  /** The chat session the thoughts are addressed with, never the specialist's own. */
  sessionKey: string;
  chatId: string;
  /** The specialist doing the thinking. */
  agentId: string;
  /** Who it works for; the panel folds this specialist's blocks under that agent's. */
  parentAgentId: string | null;
  /** One message id for the whole run: the turn that finally carries its reasoning. */
  messageId: string;
  account: ScwUiAccount;
  /** Cumulative reasoning seen so far, for diffing the next event down to a delta. */
  prior: string;
  /** Delta since the last flush. */
  buf: string;
  seq: number;
  /** Anything actually sent, i.e. whether there is a turn to close. */
  posted: boolean;
  /** Private FIFO so the turn that closes the run cannot overtake its own thoughts. */
  chain: Promise<unknown>;
  at: number;
}

const liveRuns = getSharedState("thinking-stream:live-runs", () => new Map<string, LiveRun>());

/** Session key per run, learned from `lifecycle` — the one stream that always carries it. */
const sessionKeyByRun = getSharedState(
  "thinking-stream:session-key-by-run",
  () => new Map<string, { sessionKey: string; at: number }>(),
);

/** Runs decided not to be ours, so the decision is made once and not per event. */
const ignoredRuns = getSharedState(
  "thinking-stream:ignored-runs",
  () => new Map<string, number>(),
);

/**
 * Runs this module has already posted for, kept after the run is gone.
 *
 * `liveRuns` answers "am I working on this one", but only until the run is closed; the relay can
 * see a finished run again (`agent_end` fires per attempt, so it can fire after the terminal
 * lifecycle event). This map is the lasting half of that answer.
 */
const streamedRuns = getSharedState("thinking-stream:streamed-runs", () => new Map<string, number>());

/** Runs whose turn has been closed, so a second finish is a no-op. */
const finishedRuns = getSharedState("thinking-stream:finished-runs", () => new Map<string, number>());

const lastSweep = getSharedState("thinking-stream:last-sweep", () => ({ at: 0 }));

function sweep(now: number): void {
  if (now - lastSweep.at < SWEEP_EVERY_MS) return;
  lastSweep.at = now;
  const bounded: Array<[Map<string, unknown>, number]> = [
    [sessionKeyByRun as Map<string, unknown>, MAX_MARKS],
    [ignoredRuns as Map<string, unknown>, MAX_MARKS],
    [streamedRuns as Map<string, unknown>, MAX_MARKS],
    [finishedRuns as Map<string, unknown>, MAX_MARKS],
    [liveRuns as Map<string, unknown>, MAX_LIVE_RUNS],
  ];
  for (const [map, limit] of bounded) {
    for (const [key, entry] of map) {
      const at = typeof entry === "number" ? entry : (entry as { at: number }).at;
      if (now - at > RUN_TTL_MS) map.delete(key);
    }
    // Insertion order, so this drops the runs that went quiet longest ago.
    while (map.size > limit) {
      const oldest = map.keys().next();
      if (oldest.done) break;
      map.delete(oldest.value);
    }
  }
}

/**
 * Whether this run's reasoning belongs to the live stream, so the relay must stay quiet about it.
 *
 * True from the first thinking event, NOT from the first post — a run whose whole reasoning fits
 * under the flush threshold has posted nothing yet and still has every word of it buffered here.
 * Answering "not mine" then is what made the same paragraph appear twice: the relay read it from
 * the transcript while this module was holding it, and both spoke.
 */
export function ownsRunReasoning(runId: string): boolean {
  return runId ? liveRuns.has(runId) || streamedRuns.has(runId) : false;
}

function asString(value: unknown): string {
  return typeof value === "string" ? value : "";
}

/** Post one item, and remember that this run now has a turn to close. */
function post(api: OpenClawPluginApi, runId: string, run: LiveRun, raw: string): void {
  const text = raw.trim();
  if (!text) return;
  const seq = run.seq++;
  run.posted = true;
  streamedRuns.set(runId, Date.now());
  run.chain = run.chain.then(
    async () =>
      await postThought(run.account, run.sessionKey, run.chatId, {
        message_id: run.messageId,
        agent_id: run.agentId,
        ...(run.parentAgentId ? { parent_agent_id: run.parentAgentId } : {}),
        kind: "text",
        text,
        seq,
      }).catch((err: unknown) => {
        logError(
          api,
          `sellerclaw-ui: live thought post failed run=${runId} ` +
            `session_key=${run.sessionKey}: ${String(err)}`,
        );
      }),
  );
}

/** Cut the buffer on a line boundary so an item never splits mid-sentence. */
function flushIfFull(api: OpenClawPluginApi, runId: string, run: LiveRun): void {
  if (run.buf.length < FLUSH_AT) return;
  const newline = run.buf.lastIndexOf("\n");
  const cut = newline > 0 ? newline : run.buf.length;
  post(api, runId, run, run.buf.slice(0, cut));
  run.buf = run.buf.slice(cut);
}

/**
 * The run's bookkeeping, created on the first thinking event we mean to report.
 *
 * Returns `null` for every run that is not a specialist working for one of our chats — the
 * majority on a busy gateway — and remembers that verdict so the next event is free.
 */
function resolveRun(
  api: OpenClawPluginApi,
  runId: string,
  event: AgentEventPayload,
): LiveRun | null {
  const existing = liveRuns.get(runId);
  if (existing) {
    existing.at = Date.now();
    return existing;
  }
  if (ignoredRuns.has(runId) || finishedRuns.has(runId)) return null;
  const now = Date.now();
  const ignore = (): null => {
    ignoredRuns.set(runId, now);
    return null;
  };
  // `sessionKey` is stripped from non-lifecycle streams for our channel, so the lifecycle map is
  // the real source here; the event's own value is only a shortcut when the host does stamp it.
  const sessionKey = asString(event.sessionKey) || sessionKeyByRun.get(runId)?.sessionKey || "";
  if (!sessionKey) return ignore();
  // A key that resolves to an address is a chat session — the dispatch and the relay own those,
  // and that verdict will never change, so remember it.
  if (extractTargetFromSessionKey(sessionKey)) return ignore();
  const origin = lookupSubagentOrigin(sessionKey, now);
  if (!origin) {
    // No address for this session YET. Deliberately not remembered: the spawn that would supply
    // one is announced by a hook, and nothing guarantees that hook runs before the child's first
    // thinking event. Caching a "no" here would silence a specialist for the whole run over an
    // ordering accident; re-asking costs one map lookup.
    return null;
  }
  let account: ScwUiAccount;
  try {
    account = resolveSellerclawUiAccount(api.config);
  } catch (err) {
    logError(api, `sellerclaw-ui: live thinking cannot resolve account: ${String(err)}`);
    return ignore();
  }
  const run: LiveRun = {
    sessionKey: origin.requesterSessionKey,
    chatId: origin.chatId,
    agentId: asString(event.agentId) || origin.agentId,
    parentAgentId: origin.parentAgentId ?? null,
    messageId: crypto.randomUUID(),
    account,
    prior: "",
    buf: "",
    seq: 0,
    posted: false,
    chain: Promise.resolve(),
    at: now,
  };
  liveRuns.set(runId, run);
  logInfo(
    api,
    `sellerclaw-ui: streaming ${run.agentId} thinking to chat ${run.chatId} (run ${runId})`,
  );
  return run;
}

/**
 * Close the run: flush what is left, then open and close an empty turn to carry the reasoning.
 *
 * The empty turn is the same mechanism the relay uses — the cloud drops a turn that produced no
 * part and hands its reasoning to the answer of this exchange, or parks it for the answer still
 * being composed. It is posted only at the end on purpose: opening a turn creates a streaming
 * message row that can adopt the owner's pending question, which would take the reply slot from
 * the real answer. Thought posts have no such effect — the cloud keeps them in Redis and
 * publishes them live without a message row.
 *
 * Idempotent, and called from both the run's terminal lifecycle event and `agent_end`, because
 * either one can be the last thing we see.
 */
export function finishStreamedRun(api: OpenClawPluginApi, runId: string): void {
  if (!runId) return;
  sessionKeyByRun.delete(runId);
  ignoredRuns.delete(runId);
  const run = liveRuns.get(runId);
  if (!run) return;
  liveRuns.delete(runId);
  finishedRuns.set(runId, Date.now());
  const rest = run.buf;
  run.buf = "";
  post(api, runId, run, rest);
  if (!run.posted) return;
  void run.chain
    .then(async () => {
      await postTurnStart(run.account, run.sessionKey, run.messageId, run.chatId);
      await postTurnEnd(run.account, run.sessionKey, run.messageId, run.chatId);
    })
    .catch((err: unknown) => {
      logError(
        api,
        `sellerclaw-ui: live thinking could not close its turn run=${runId} ` +
          `session_key=${run.sessionKey}: ${String(err)}`,
      );
    });
}

function onLifecycle(api: OpenClawPluginApi, runId: string, event: AgentEventPayload): void {
  const phase = asString(event.data?.phase);
  if (phase === "start") {
    const sessionKey = asString(event.sessionKey);
    if (sessionKey) sessionKeyByRun.set(runId, { sessionKey, at: Date.now() });
    return;
  }
  if (phase === "end" || phase === "error") finishStreamedRun(api, runId);
}

function onThinking(api: OpenClawPluginApi, runId: string, event: AgentEventPayload): void {
  // Two shapes reach us: a growing cumulative `text` (the native runner) and a bare `delta`.
  // Events carrying neither — a progress tick with a token count — say nothing to report.
  const text = asString(event.data?.text).trim();
  const delta = asString(event.data?.delta);
  if (!text && !delta.trim()) return;
  const run = resolveRun(api, runId, event);
  if (!run) return;
  if (text) {
    if (text === run.prior) return;
    run.buf += text.startsWith(run.prior) ? text.slice(run.prior.length) : text;
    run.prior = text;
  } else {
    run.buf += delta;
  }
  flushIfFull(api, runId, run);
}

/**
 * Install the subscription. Registered on every registry pass, like the hooks: each activation
 * builds a fresh registry and the bridge only dispatches to the current one.
 */
export function registerLiveThinkingStream(api: OpenClawPluginApi): void {
  // The nested facade is what the current SDK documents; the flat member is what it delegates to.
  // Reading both keeps this working either way, and lets a test pass the simpler shape.
  const register =
    api.agent?.events?.registerAgentEventSubscription ?? api.registerAgentEventSubscription;
  if (typeof register !== "function") {
    logWarn(api, "sellerclaw-ui: live thinking not installed (agent event subscriptions absent)");
    return;
  }
  register({
    id: "sellerclaw-ui-live-thinking",
    description: "Streams a specialist's reasoning to the owner's chat while it works.",
    streams: ["lifecycle", "thinking"],
    handle: (event: AgentEventPayload) => {
      const runId = asString(event?.runId);
      if (!runId) return;
      sweep(Date.now());
      try {
        if (event.stream === "lifecycle") onLifecycle(api, runId, event);
        else if (event.stream === "thinking") onThinking(api, runId, event);
      } catch (err) {
        logError(api, `sellerclaw-ui: live thinking handler failed run=${runId}: ${String(err)}`);
      }
    },
  });
  logInfo(api, "sellerclaw-ui: live thinking stream installed");
}

/** Test-only: drop every tracked run so cases cannot leak into each other. */
export function __resetThinkingStream(): void {
  for (const map of [liveRuns, sessionKeyByRun, ignoredRuns, streamedRuns, finishedRuns]) {
    map.clear();
  }
}
