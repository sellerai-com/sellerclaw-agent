import type { OpenClawPluginApi } from "openclaw/plugin-sdk/core";

import { deliverTextToChat, extractTargetFromSessionKey, resolveSellerclawUiAccount } from "./channel.js";
import { ANNOUNCE_RUN_ID_PREFIX, isCompletionRun } from "./completion-run.js";
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

/** Bound on remembered messaging-tool runs, so a long-lived gateway cannot accumulate them. */
const MESSAGED_RUN_TTL_MS = 10 * 60_000;

/**
 * How long a run's captured visible text stays readable by its own `agent_end`.
 *
 * A run's last model call and its end are moments apart; this only has to outlive a long final
 * tool call, and expiring keeps a run that never ends (killed gateway) from handing its text to
 * an unrelated later run in the same chat.
 */
const LAST_VISIBLE_TEXT_TTL_MS = 15 * 60_000;

/** The silent token: an answer of exactly this means "deliver nothing". */
const SILENT_REPLY_TOKEN = "NO_REPLY";

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

/** Run ids (or session keys, when no run id is available) that already messaged the owner. */
const messagedRuns = getSharedState(
  "completion-delivery:messaged-runs",
  () => new Map<string, number>(),
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
  () => new Map<string, { text: string; runId: string; completion: boolean; expiresAt: number }>(),
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

function isSilentAnswer(text: string): boolean {
  return text.trim().toUpperCase() === SILENT_REPLY_TOKEN;
}

function sweepMessagedRuns(now: number): void {
  for (const [key, expiresAt] of messagedRuns) {
    if (expiresAt <= now) messagedRuns.delete(key);
  }
}

function messagedKeys(runId: unknown, sessionKey: string): string[] {
  const keys = [`session:${sessionKey}`];
  const run = asString(runId);
  if (run) keys.unshift(`run:${run}`);
  return keys;
}

function hasMessagedOwner(runId: unknown, sessionKey: string): boolean {
  const now = Date.now();
  sweepMessagedRuns(now);
  return messagedKeys(runId, sessionKey).some((key) => (messagedRuns.get(key) ?? 0) > now);
}

/** The run's latest visible assistant text and the run it came from, while still applicable. */
function readLastVisible(
  sessionKey: string,
): { text: string; runId: string; completion: boolean } | null {
  const entry = lastVisibleTexts.get(sessionKey);
  if (!entry) return null;
  if (entry.expiresAt <= Date.now()) {
    lastVisibleTexts.delete(sessionKey);
    return null;
  }
  return { text: entry.text, runId: entry.runId, completion: entry.completion };
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
    if (!sessionKey || !extractTargetFromSessionKey(sessionKey)) return undefined;
    const expiresAt = Date.now() + MESSAGED_RUN_TTL_MS;
    for (const key of messagedKeys(event?.runId ?? ctx?.runId, sessionKey)) {
      messagedRuns.set(key, expiresAt);
    }
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
    lastVisibleTexts.set(sessionKey, {
      text: texts.join("\n\n"),
      runId,
      // Only the run-id prefix is available here (``llm_output`` carries no transcript), which
      // covers every announce run the runtime drives. The trigger-text fallback still applies at
      // ``agent_end``, where the messages are.
      completion: runId.startsWith(ANNOUNCE_RUN_ID_PREFIX),
      expiresAt: Date.now() + LAST_VISIBLE_TEXT_TTL_MS,
    });
    return undefined;
  });

  // Run end is the decision point: the last visible text of the run is the answer, and no visible
  // text at all means there is nothing to substitute — only the child's report to stop. Both land
  // before the runtime's fallback delivery, which is what ``message_sending`` then intercepts.
  api.on?.("agent_end", (event: AgentEndEvent, ctx?: HookContext) => {
    const sessionKey = asString(ctx?.sessionKey);
    if (!sessionKey || !extractTargetFromSessionKey(sessionKey)) return undefined;
    const answer = readLastVisible(sessionKey)?.text ?? "";
    lastVisibleTexts.delete(sessionKey);
    if (!isCompletionRun(event?.runId ?? ctx?.runId, event?.messages)) return undefined;
    if (hasMessagedOwner(event?.runId ?? ctx?.runId, sessionKey)) {
      // The run did the right thing on its own; the runtime will not fall back at all.
      clearPending(sessionKey);
      return undefined;
    }
    if (!answer || isSilentAnswer(answer)) {
      rememberAnswer(api, sessionKey, "");
      logDeliveryFailure(api, `completion run produced no answer session_key=${sessionKey}`);
      return undefined;
    }
    rememberAnswer(api, sessionKey, answer);
    logDelivery(
      api,
      `completion answer captured for delivery session_key=${sessionKey} chars=${answer.length}`,
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
 * must pass through untouched.
 */
function adoptRacedCompletionAnswer(
  api: OpenClawPluginApi,
  sessionKey: string,
): PendingAnswer | null {
  const seen = readLastVisible(sessionKey);
  if (!seen || !seen.completion || !seen.text) return null;
  if (hasMessagedOwner(seen.runId, sessionKey)) return null;
  lastVisibleTexts.delete(sessionKey);
  rememberAnswer(api, sessionKey, seen.text);
  logDelivery(
    api,
    `completion answer captured at delivery time (agent_end had not landed) ` +
      `session_key=${sessionKey} chars=${seen.text.length}`,
  );
  return readPending(sessionKey);
}

function registerDeliveryRewrite(api: OpenClawPluginApi): void {
  api.on?.(
    "message_sending",
    (event: MessageSendingEvent, ctx?: HookContext): MessageSendingResult | undefined => {
      const sessionKey = asString(ctx?.sessionKey);
      if (!sessionKey) return undefined;
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
      if (!pending.answer) {
        clearPending(sessionKey);
        logDeliveryFailure(
          api,
          `raw subagent report suppressed, owner gets nothing session_key=${sessionKey}`,
        );
        return { cancel: true, cancelReason: "completion run produced no owner-facing answer" };
      }
      const answer = pending.answer;
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
}
