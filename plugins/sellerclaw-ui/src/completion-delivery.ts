import type { OpenClawPluginApi } from "openclaw/plugin-sdk/core";

import {
  deliverTextToChat,
  extractTargetFromSessionKey,
  normalizeSellerclawUiTarget,
  resolveSellerclawUiAccount,
} from "./channel.js";
import { ANNOUNCE_RUN_ID_PREFIX, isCompletionRun } from "./completion-run.js";
import { droppedSilentProse, isSilentAnswer, visibleAnswerText } from "./silent-token.js";
import { logDelivery, logDeliveryFailure } from "./log.js";
import { getSharedState } from "./shared-state.js";

/**
 * Makes a completion run's answer reach the owner without the agent having to send it.
 *
 * The runtime refuses to deliver a completion run's ordinary reply text when the requester is
 * a direct message — and our chat address, ``sellerclaw-ui:direct:<chat_id>``, always is. The
 * condition (``subagentDirectMessageCompletionRequiresMessageTool`` in
 * ``subagent-announce-delivery.ts``) is computed, not configured: no setting turns it off, and
 * ``visibleReplies: "automatic"`` is checked on a different branch that never reaches this one.
 * Having suppressed the answer, the runtime then fills the silence itself — it sends the
 * *child's* raw ``task_completion.result`` to the owner, an internal envelope written for the
 * supervisor, in whatever language the specialist happened to use. That is upstream bug
 * openclaw#90840: open, P1, and unresolved because the clean fix needs a product decision they
 * cannot make for every deployment. We can make it for ours: the subagents are the owner's own
 * and so is the data, so the supervisor's answer is exactly what should be shown.
 *
 * So this module lets the runtime keep its delivery, and corrects what it carries:
 *
 *  1. ``before_tool_call`` — remember that the agent sent something itself, so a run that
 *     complied is left completely alone.
 *  2. ``llm_output`` — the visible text of each model call, kept as the run's answer-so-far.
 *     Deliberately not ``before_agent_finalize``, which would hand the final text ready-made:
 *     merely having a handler for that hook makes 2026.8 hold back the entire visible reply
 *     stream until the run ends (see ``registerAnswerCapture``), and live streaming is worth
 *     more than the convenience.
 *  3. ``agent_end`` — the decision point. The last visible text of the run is the answer to
 *     substitute; a run that produced none has nothing to show, so the fallback must be stopped
 *     rather than corrected.
 *  4. ``message_sending`` — the interception point. Runs inside the shared outbound path
 *     (``deliverOutboundPayloads``) before the channel adapter is called, and may rewrite the
 *     text or cancel the send. A cancelled send is recorded as ``suppressed``, which the
 *     durable-send layer commits as a success — so cancelling costs no retries and does not
 *     fail the announce.
 *
 * If the runtime delivers nothing at all (upstream fixes #90840, or the announce path changes
 * shape), a short timer sends the answer ourselves. That is why this does not depend on the
 * bug staying unfixed.
 */

/** Tools that put a message in front of the owner on their own. */
const MESSAGING_TOOL_NAMES = new Set(["message", "conversations_send"]);

/**
 * How long a captured answer stays applicable. The fallback send follows finalization within
 * moments, so this only has to outlive one announce round-trip; keeping it short means a later,
 * unrelated delivery in the same chat can never pick up a stale answer.
 */
const PENDING_TTL_MS = 60_000;

/**
 * Shorter window for the "nothing to show" entry. It suppresses rather than substitutes, and
 * the fallback it is waiting for follows finalization immediately — so anything beyond a few
 * seconds is pure risk of cancelling an unrelated send the owner did want (a `message` from a
 * fresh chat turn, say).
 */
const EMPTY_PENDING_TTL_MS = 15_000;

/**
 * Grace period before the plugin delivers the answer itself. Long enough for the runtime's own
 * delivery to arrive first (it normally does, within milliseconds of finalization), short
 * enough that the owner is not left waiting on a path that will never fire.
 */
const SELF_DELIVER_AFTER_MS = 5_000;

/** How long a self-delivered entry lingers, purely to swallow a late runtime delivery. */
const SUPPRESS_AFTER_SELF_DELIVERY_MS = 30_000;

/**
 * Bound on remembered messaging-tool runs, so a long-lived gateway cannot accumulate them.
 *
 * The TTL is a growth bound, not a validity window: each marker records which run made the
 * send, and ``hasMessagedOwner`` refuses a marker provably left by a *different* run. Before
 * that attribution existed, a marker's mere freshness was taken as proof — and a card the
 * supervisor had messaged from a live chat turn vouched, seven minutes later, for an announce
 * run that had messaged nobody. The guard stood down and the owner read the Wix specialist's
 * raw completion envelope (staging chat cc713e8d, 2026-08-30).
 */
const MESSAGED_RUN_TTL_MS = 10 * 60_000;

/**
 * How long a run's captured visible text stays readable by its own `agent_end`.
 *
 * A run's last model call and its end are moments apart; this only has to outlive a long final
 * tool call, and expiring keeps a run that never ends (killed gateway) from handing its text to
 * an unrelated later run in the same chat.
 */
const LAST_VISIBLE_TEXT_TTL_MS = 15 * 60_000;

/**
 * How recently that text must have been captured for a *delivery* to claim it as its own answer.
 *
 * The race ``adoptRacedCompletionAnswer`` rescues is a dispatch ordering inside a single run —
 * ``agent_end`` voided by the harness, the final ``llm_output`` landing a heartbeat later — so the
 * gap it has to cover is milliseconds. The TTL above is a different measure: it bounds how long a
 * run's own ``agent_end`` may take to arrive, and fifteen minutes of it is what turned a stale
 * entry into somebody else's answer. A duplicate announce answered ``NO_REPLY``, its late
 * ``llm_output`` refilled the map after its own ``agent_end`` had emptied it, and twelve minutes
 * later the owner asked an ordinary question in the same chat: the delivery adopted the leftover
 * token and the answer the agent had actually written — 492 characters of it — was replaced by the
 * word ``NO_REPLY`` on its way out (staging chat 4148ca42, 2026-08-27).
 *
 * Age is the only discriminator applied at delivery time: ``message_sending`` is an outbound-path
 * hook whose context is not promised to name the producing run, so a run-id comparison there could
 * veto a legitimate rescue. Run ids are compared at ``agent_end`` instead, where they are
 * comparable — see ``adoptRacedCompletionAnswer``.
 */
const RACED_ANSWER_MAX_AGE_MS = 10_000;

/**
 * How long a ``message``-tool send stays recognizable on its way through ``message_sending``.
 *
 * ``before_tool_call`` and the outbound hook for the same send are microseconds apart in one
 * process, so this only has to absorb scheduler jitter. It must stay short: while it is open, a
 * runtime fallback arriving in the same chat would be waved through as if the agent had sent it.
 */
const TOOL_SEND_GRACE_MS = 3_000;

interface PendingAnswer {
  /** The supervisor's answer, or "" when the run produced no visible text at all. */
  answer: string;
  expiresAt: number;
  /** Set once the plugin delivered the answer itself; the entry then only suppresses. */
  deliveredBySelf: boolean;
  timer: ReturnType<typeof setTimeout> | null;
}

/**
 * Keyed by session key — the only identifier both the run hooks and delivery hooks share.
 *
 * Held in a process-wide slot rather than a module-level map: OpenClaw re-evaluates the plugin
 * module on every registry pass, so a module-local map would be empty in the pass whose hooks
 * are live, losing the captured answer (and the post-rescue suppression window) whenever a
 * registry rebuild lands between capture and delivery. See `shared-state.ts`.
 */
const pendingAnswers = getSharedState(
  "completion-delivery:pending-answers",
  () => new Map<string, PendingAnswer>(),
);

/**
 * Runs that already messaged the owner, and which run each marker belongs to.
 *
 * Keyed by run id and, as a fallback for hooks whose context names no run, by session key.
 * The session-scoped entry still records the sending run (``""`` when unknown) so a later run
 * in the same chat cannot inherit it — see ``hasMessagedOwner``.
 */
const messagedRuns = getSharedState(
  "completion-delivery:messaged-runs",
  () => new Map<string, { expiresAt: number; runId: string }>(),
);

/**
 * Latest visible assistant text per session, filled per model call and read once at run end.
 *
 * The window only has to bridge the gap between the run's last model call and its ``agent_end``
 * — moments — but a run whose end never fires (a killed gateway) must not leave the text behind
 * for an unrelated later run to adopt as its answer.
 */
const lastVisibleTexts = getSharedState(
  "completion-delivery:last-visible-texts",
  () =>
    new Map<
      string,
      { text: string; runId: string; completion: boolean; capturedAt: number; expiresAt: number }
    >(),
);

/**
 * Sessions with a ``message``-tool send on its way to the outbound hook, by when it was made.
 *
 * The guard's business is correcting what the *runtime* delivers; a send the agent made itself
 * must pass through untouched. The two are indistinguishable inside ``message_sending`` — its
 * context names no run — except by this: a tool send always crosses ``before_tool_call``
 * microseconds earlier, and a runtime fallback never does. Without the flag, a pending answer
 * left by one run could rewrite or cancel the next run's own legitimate message: a cancelled
 * send is committed as *success*, so the agent would believe it delivered a message the owner
 * never saw. Consumed one-shot by the first delivery it excuses, keyed by the send's target
 * session (a cross-chat send is excused in the chat it lands in).
 */
const toolSendsInFlight = getSharedState(
  "completion-delivery:tool-sends-in-flight",
  () => new Map<string, number>(),
);

interface LlmOutputEvent {
  runId?: unknown;
  assistantTexts?: unknown;
}

interface AgentEndEvent {
  runId?: unknown;
  messages?: unknown;
}

interface BeforeToolCallEvent {
  toolName?: unknown;
  params?: unknown;
  runId?: unknown;
}

interface MessageSendingEvent {
  to?: unknown;
  content?: unknown;
}

interface HookContext {
  sessionKey?: unknown;
  runId?: unknown;
}

interface MessageSendingResult {
  content?: string;
  cancel?: boolean;
  cancelReason?: string;
}

function asString(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function sweepMessagedRuns(now: number): void {
  for (const [key, marker] of messagedRuns) {
    if (marker.expiresAt <= now) messagedRuns.delete(key);
  }
}

function messagedKeys(runId: unknown, sessionKey: string): string[] {
  const keys = [`session:${sessionKey}`];
  const run = asString(runId);
  if (run) keys.unshift(`run:${run}`);
  return keys;
}

/**
 * Whether *this* run put a message in front of the owner.
 *
 * The run-scoped marker is authoritative. The session-scoped one is a fallback for hooks whose
 * context named no run — so it vouches only when neither side can be attributed, or when it was
 * recorded by the very run now asking. A marker provably left by a different run in the same
 * chat says nothing about this one: honoring it is how a live turn's card message once disarmed
 * the guard for the announce run that followed, and the owner got the child's raw envelope.
 */
function hasMessagedOwner(runId: unknown, sessionKey: string): boolean {
  const now = Date.now();
  sweepMessagedRuns(now);
  const asking = asString(runId);
  if (asking && (messagedRuns.get(`run:${asking}`)?.expiresAt ?? 0) > now) return true;
  const bySession = messagedRuns.get(`session:${sessionKey}`);
  if (!bySession || bySession.expiresAt <= now) return false;
  return !asking || !bySession.runId || bySession.runId === asking;
}

/** The run's latest visible assistant text and the run it came from, while still applicable. */
function readLastVisible(
  sessionKey: string,
): { text: string; runId: string; completion: boolean; capturedAt: number } | null {
  const entry = lastVisibleTexts.get(sessionKey);
  if (!entry) return null;
  if (entry.expiresAt <= Date.now()) {
    lastVisibleTexts.delete(sessionKey);
    return null;
  }
  return {
    text: entry.text,
    runId: entry.runId,
    completion: entry.completion,
    capturedAt: entry.capturedAt,
  };
}

function clearPending(sessionKey: string): void {
  const pending = pendingAnswers.get(sessionKey);
  if (pending?.timer) clearTimeout(pending.timer);
  pendingAnswers.delete(sessionKey);
}

function readPending(sessionKey: string): PendingAnswer | null {
  const pending = pendingAnswers.get(sessionKey);
  if (!pending) return null;
  if (pending.expiresAt <= Date.now()) {
    clearPending(sessionKey);
    return null;
  }
  return pending;
}

/**
 * Send the captured answer ourselves, then keep the entry briefly so a late runtime delivery
 * of the child's raw text is swallowed instead of arriving as a second message.
 */
async function selfDeliver(api: OpenClawPluginApi, sessionKey: string): Promise<void> {
  const pending = readPending(sessionKey);
  if (!pending || pending.deliveredBySelf || !pending.answer) return;
  let account;
  try {
    account = resolveSellerclawUiAccount(api.config);
  } catch (err) {
    logDeliveryFailure(
      api,
      `cannot resolve account for rescue send session_key=${sessionKey}: ${String(err)}`,
    );
    clearPending(sessionKey);
    return;
  }
  try {
    await deliverTextToChat(account, sessionKey, pending.answer);
    pending.deliveredBySelf = true;
    pending.timer = null;
    pending.expiresAt = Date.now() + SUPPRESS_AFTER_SELF_DELIVERY_MS;
    logDelivery(api, `rescue send completed session_key=${sessionKey}`);
  } catch (err) {
    logDeliveryFailure(api, `rescue send failed session_key=${sessionKey}: ${String(err)}`);
    clearPending(sessionKey);
  }
}

function rememberAnswer(api: OpenClawPluginApi, sessionKey: string, answer: string): void {
  clearPending(sessionKey);
  const pending: PendingAnswer = {
    answer,
    expiresAt: Date.now() + (answer ? PENDING_TTL_MS : EMPTY_PENDING_TTL_MS),
    deliveredBySelf: false,
    timer: null,
  };
  if (answer) {
    const timer = setTimeout(() => {
      void selfDeliver(api, sessionKey);
    }, SELF_DELIVER_AFTER_MS);
    // Never hold the process open for a rescue send. The cast is for the local type-check
    // only: this package has no Node typings, so `setTimeout` is typed as the DOM one, while
    // inside the gateway container it is a Node timer that does have `unref`.
    (timer as unknown as { unref?: () => void }).unref?.();
    pending.timer = timer;
  }
  pendingAnswers.set(sessionKey, pending);
}

function registerMessagingToolTracker(api: OpenClawPluginApi): void {
  api.on?.("before_tool_call", (event: BeforeToolCallEvent, ctx?: HookContext) => {
    const toolName = asString(event?.toolName).trim();
    if (!MESSAGING_TOOL_NAMES.has(toolName)) return undefined;
    const params = (event?.params ?? {}) as Record<string, unknown>;
    // `message` carries an action; anything other than a send leaves the owner unaddressed.
    // `conversations_send` has no action field — the name is the action.
    const action = asString(params.action).trim().toLowerCase();
    if (toolName === "message" && action && action !== "send") return undefined;
    const sessionKey = asString(ctx?.sessionKey);
    const ownAddress = sessionKey ? extractTargetFromSessionKey(sessionKey) : null;
    if (!sessionKey || !ownAddress) return undefined;
    const run = asString(event?.runId ?? ctx?.runId);
    const marker = { expiresAt: Date.now() + MESSAGED_RUN_TTL_MS, runId: run };
    for (const key of messagedKeys(run, sessionKey)) {
      messagedRuns.set(key, marker);
    }
    // Flag the send for ``message_sending`` under the session it will be delivered into: the
    // current one when the tool has no explicit target (the default), the target chat's
    // otherwise — both agent-prefixed keys share everything but the address. A flag whose
    // delivery never came through (the send failed before the outbound hook) is swept here,
    // long after it could excuse anything, so the map cannot grow without bound.
    const now = Date.now();
    for (const [key, markedAt] of toolSendsInFlight) {
      if (now - markedAt > MESSAGED_RUN_TTL_MS) toolSendsInFlight.delete(key);
    }
    const targetAddress =
      normalizeSellerclawUiTarget(asString(params.target ?? params.to)) ?? ownAddress;
    toolSendsInFlight.set(sessionKey.replace(ownAddress, targetAddress), now);
    return undefined;
  });
}

function registerAnswerCapture(api: OpenClawPluginApi): void {
  // Every model call of every run of our chats leaves its visible text here. Deliberately NOT
  // ``before_agent_finalize``, which hands the final text ready-made: registering *any* handler
  // for that hook makes OpenClaw 2026.8 defer the whole visible reply stream to the end of the
  // run (``deferBlockReplyDelivery`` is set from ``typeof onBeforeTerminalDelivery === "function"``
  // in ``embedded-agent-subscribe``), so the owner watches a silent chat until the run finishes
  // and the answer lands in one lump. Reading the same text off ``llm_output`` costs one map
  // entry and keeps replies streaming live.
  api.on?.("llm_output", (event: LlmOutputEvent, ctx?: HookContext) => {
    const sessionKey = asString(ctx?.sessionKey);
    if (!sessionKey || !extractTargetFromSessionKey(sessionKey)) return undefined;
    const texts = Array.isArray(event?.assistantTexts)
      ? event.assistantTexts.map((text) => asString(text).trim()).filter((text) => text.length > 0)
      : [];
    if (texts.length === 0) return undefined;
    const runId = asString(event?.runId);
    const now = Date.now();
    lastVisibleTexts.set(sessionKey, {
      text: texts.join("\n\n"),
      runId,
      // Only the run-id prefix is available here (``llm_output`` carries no transcript), which
      // covers every announce run the runtime drives. The trigger-text fallback still applies at
      // ``agent_end``, where the messages are.
      completion: runId.startsWith(ANNOUNCE_RUN_ID_PREFIX),
      capturedAt: now,
      expiresAt: now + LAST_VISIBLE_TEXT_TTL_MS,
    });
    return undefined;
  });

  // Run end is the decision point: the last visible text of the run is the answer, and no visible
  // text at all means there is nothing to substitute — only the child's report to stop. Both land
  // before the runtime's fallback delivery, which is what ``message_sending`` then intercepts.
  api.on?.("agent_end", (event: AgentEndEvent, ctx?: HookContext) => {
    const sessionKey = asString(ctx?.sessionKey);
    if (!sessionKey || !extractTargetFromSessionKey(sessionKey)) return undefined;
    const ending = asString(event?.runId ?? ctx?.runId);
    const seen = readLastVisible(sessionKey);
    // This run's own text, and nobody else's. A previous run of the same chat can leave an entry
    // behind — its final ``llm_output`` landing after its own end refills the map that end had just
    // emptied — and adopting that would answer this run with the last one's words. The id filter is
    // best-effort (two announce runs are not guaranteed distinguishable ids); the capture-age check
    // in ``adoptRacedCompletionAnswer`` is what backstops it at delivery time.
    const answer = seen && (!ending || !seen.runId || seen.runId === ending) ? seen.text : "";
    lastVisibleTexts.delete(sessionKey);
    if (!isCompletionRun(event?.runId ?? ctx?.runId, event?.messages)) return undefined;
    if (hasMessagedOwner(event?.runId ?? ctx?.runId, sessionKey)) {
      // The run did the right thing on its own; the runtime will not fall back at all.
      clearPending(sessionKey);
      return undefined;
    }
    if (isSilentAnswer(answer)) {
      // Remembered as empty rather than left unset, so the runtime's fallback is still recognised
      // as belonging to this run and gets cancelled instead of reaching the owner as raw internals.
      // Whether there is really nothing to say is decided at delivery time, once ``llm_output``
      // for the final round has landed — see ``registerDeliveryRewrite``.
      rememberAnswer(api, sessionKey, "");
      const dropped = droppedSilentProse(answer);
      // Recorded because this is the one branch that can lose something the owner wanted: the run
      // asked for silence, and whatever it wrote before the token goes no further. Nothing has been
      // seen doing that with a real report — but if one ever does, this line is where it shows.
      logDeliveryFailure(
        api,
        dropped
          ? `silent run: dropped its own text before the token session_key=${sessionKey} ` +
            `chars=${dropped.length}`
          : `no answer at run end (may land with the final llm_output) session_key=${sessionKey}`,
      );
      return undefined;
    }
    const visible = visibleAnswerText(answer);
    rememberAnswer(api, sessionKey, visible);
    logDelivery(
      api,
      `completion answer captured for delivery session_key=${sessionKey} chars=${visible.length}`,
    );
    return undefined;
  });
}

/**
 * Capture the answer here when the runtime's fallback beat ``agent_end`` to the punch.
 *
 * ``agent_end`` is dispatched fire-and-forget by the harness (``runAgentEndSideEffects`` voids
 * the promise), so the run's decision point is not ordered against this send. It practically
 * always wins — the handler is synchronous and the fallback goes through announce plumbing — but
 * "practically" is not a guarantee, and losing the race means the owner sees the child's raw
 * internal envelope, which is the one thing this module exists to prevent.
 *
 * Restricted to announce runs that never messaged the owner: a normal chat turn's own delivery
 * must pass through untouched. Being an announce run is a property of the *captured* text, though,
 * not of the delivery reading it — so the text must also be shown to belong to the delivery
 * claiming it. Capture *age* is what shows that: the race this rescues is milliseconds wide, so
 * anything older than a few seconds is by definition a leftover from an earlier run of the same
 * chat. Without that check a live question inherited a twelve-minute-old ``NO_REPLY`` and lost its
 * own answer — see ``RACED_ANSWER_MAX_AGE_MS``. Deliberately NOT the run id: ``message_sending``
 * is an outbound-path hook, not a run event, and nothing promises its context names the run whose
 * ``llm_output`` wrote the text — comparing ids here could reject a perfectly legitimate rescue
 * and hand the owner an empty chat, the very failure this module exists to prevent. The id check
 * lives at ``agent_end``, where both sides come from run-scoped events and are comparable.
 */
function adoptRacedCompletionAnswer(
  api: OpenClawPluginApi,
  sessionKey: string,
): PendingAnswer | null {
  const seen = readLastVisible(sessionKey);
  if (!seen || !seen.completion || !seen.text) return null;
  if (Date.now() - seen.capturedAt > RACED_ANSWER_MAX_AGE_MS) return null;
  if (hasMessagedOwner(seen.runId, sessionKey)) return null;
  lastVisibleTexts.delete(sessionKey);
  // The silent token is a refusal to speak, not something to say. Remembered as empty so the
  // runtime's fallback is cancelled — the outcome the run asked for — instead of the word itself
  // being handed to the owner as though it were the answer.
  const answer = isSilentAnswer(seen.text) ? "" : visibleAnswerText(seen.text);
  rememberAnswer(api, sessionKey, answer);
  logDelivery(
    api,
    `completion answer captured at delivery time (agent_end had not landed) ` +
      `session_key=${sessionKey} chars=${answer.length}`,
  );
  return readPending(sessionKey);
}

function registerDeliveryRewrite(api: OpenClawPluginApi): void {
  api.on?.(
    "message_sending",
    (event: MessageSendingEvent, ctx?: HookContext): MessageSendingResult | undefined => {
      const sessionKey = asString(ctx?.sessionKey);
      if (!sessionKey) return undefined;
      // The agent's own ``message``-tool send, recognized by the flag its ``before_tool_call``
      // raised moments ago. It is exactly what it should be — never rewrite or cancel it, and
      // leave any pending answer in place for the runtime delivery it is actually waiting for.
      const toolSendMarkedAt = toolSendsInFlight.get(sessionKey);
      if (toolSendMarkedAt !== undefined) {
        toolSendsInFlight.delete(sessionKey);
        if (Date.now() - toolSendMarkedAt <= TOOL_SEND_GRACE_MS) return undefined;
      }
      const pending = readPending(sessionKey) ?? adoptRacedCompletionAnswer(api, sessionKey);
      if (!pending) return undefined;
      const content = asString(event?.content);
      // A media-only payload arrives with empty text. Generated images and files are the
      // child's deliverables, not its internal report — they must reach the owner untouched.
      if (!content.trim()) return undefined;

      if (pending.deliveredBySelf) {
        logDelivery(api, `late runtime delivery suppressed session_key=${sessionKey}`);
        return { cancel: true, cancelReason: "answer already delivered by sellerclaw-ui" };
      }
      // ``agent_end`` may have concluded "no answer" simply because it ran first: OpenClaw
      // dispatches it before ``llm_output`` for the final model call, so a run whose whole answer
      // is written in its last round leaves the capture map empty at that moment and full a
      // heartbeat later — which is now. Asking again here is the difference between the owner
      // reading their report and reading nothing at all.
      const answered = pending.answer
        ? pending
        : (adoptRacedCompletionAnswer(api, sessionKey) ?? pending);
      if (!answered.answer) {
        clearPending(sessionKey);
        logDeliveryFailure(
          api,
          `raw subagent report suppressed, owner gets nothing session_key=${sessionKey}`,
        );
        return { cancel: true, cancelReason: "completion run produced no owner-facing answer" };
      }
      const answer = answered.answer;
      clearPending(sessionKey);
      if (content.trim() === answer.trim()) {
        // Already the right text (a future runtime that delivers the parent's own answer):
        // nothing to correct, and rewriting would only risk changing formatting.
        logDelivery(api, `runtime delivered the answer itself session_key=${sessionKey}`);
        return undefined;
      }
      logDelivery(
        api,
        `substituted completion answer for runtime fallback session_key=${sessionKey} ` +
          `fallback_chars=${content.length} answer_chars=${answer.length}`,
      );
      return { content: answer };
    },
  );
}

/**
 * Register the completion-delivery hooks. ``llm_output`` and ``agent_end`` are conversation
 * hooks, so they need ``plugins.entries.sellerclaw-ui.hooks.allowConversationAccess`` in the
 * OpenClaw config (the bundle sets it); without it OpenClaw logs a warning at load and those two
 * never run. ``before_tool_call`` and ``message_sending`` carry no such gate.
 */
export function registerCompletionDeliveryGuard(api: OpenClawPluginApi): void {
  if (typeof api.on !== "function") return;
  registerMessagingToolTracker(api);
  registerAnswerCapture(api);
  registerDeliveryRewrite(api);
}

/** Test-only: drop all cross-run state so cases cannot leak into each other. */
export function __resetCompletionDeliveryState(): void {
  for (const sessionKey of [...pendingAnswers.keys()]) clearPending(sessionKey);
  messagedRuns.clear();
  lastVisibleTexts.clear();
  toolSendsInFlight.clear();
}
