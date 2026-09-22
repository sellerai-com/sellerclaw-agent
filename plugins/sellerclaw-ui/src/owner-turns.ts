import { getSharedState } from "./shared-state.js";

/**
 * The owner's messages this process is answering: which turns are open in a chat, which owner
 * message each answers, and whether that message has had a reply yet.
 *
 * Several turns of one chat can be open at once. With the ``steer`` queue mode a message that
 * arrives mid-run goes into that run, or — when the run cannot take it (a subagent report being
 * written) — waits in the engine's own queue and runs after it. A stop has to reach all of them,
 * and a reply sent on the outbound road (the ``message`` tool, a queued run's answer) has to count
 * for the message it answers, not for whichever turn of the chat happens to ask.
 *
 * Process-wide, for the reason in ``shared-state.ts``: the inbound route, the abort route and the
 * outbound adapter need not come from the same evaluation of a module.
 */

export interface OwnerTurn {
  chatId: string;
  /** Cloud id of the owner message this turn answers; empty when the cloud sent none. */
  questionId: string;
  /** Aborted by the owner's stop; handed to the engine so it drops the message wherever it waits. */
  abort: AbortController;
}

const openTurns = getSharedState("owner-turns:open", () => new Map<string, Set<OwnerTurn>>());

/**
 * The owner message behind each message id the engine was given.
 *
 * Usually the same id: a turn dispatches under the cloud's own message id, and the engine hands
 * that id back as the reply target of every send the run makes. A catch-up re-delivery, a retry
 * after a refused admission and our own continuations dispatch under a fresh id instead, and a
 * reply to one of those must still find its way to the owner's message.
 */
const questionByEngineId = getSharedState(
  "owner-turns:engine-ids",
  () => new Map<string, { questionId: string; at: number }>(),
);

/** When each owner message last got a reply on the outbound road, by cloud id. */
const repliedAt = getSharedState("owner-turns:replied", () => new Map<string, number>());

/** How long an id is remembered; a turn only ever asks about the minutes it has been running. */
const MEMORY_MS = 60 * 60_000;

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function forgetOld<T>(entries: Map<string, T>, at: (value: T) => number, now: number): void {
  for (const [key, value] of entries) {
    if (now - at(value) > MEMORY_MS) entries.delete(key);
  }
}

export function openOwnerTurn(chatId: string, questionId: string): OwnerTurn {
  const turn: OwnerTurn = { chatId, questionId, abort: new AbortController() };
  const turns = openTurns.get(chatId) ?? new Set<OwnerTurn>();
  turns.add(turn);
  openTurns.set(chatId, turns);
  return turn;
}

export function closeOwnerTurn(turn: OwnerTurn): void {
  const turns = openTurns.get(turn.chatId);
  if (!turns) return;
  turns.delete(turn);
  if (turns.size === 0) openTurns.delete(turn.chatId);
}

/** The owner's stop: abort every turn open in the chat. Returns how many there were. */
export function abortOwnerTurns(chatId: string): number {
  const turns = openTurns.get(chatId);
  if (!turns) return 0;
  for (const turn of turns) turn.abort.abort();
  return turns.size;
}

export function rememberEngineMessageId(engineId: string, questionId: string): void {
  if (!engineId || !questionId) return;
  const now = Date.now();
  forgetOld(questionByEngineId, (v) => v.at, now);
  questionByEngineId.set(engineId, { questionId, at: now });
}

/**
 * The owner message a send replies to, from the reply target the engine gave it — or ``null`` when
 * the send answers nobody (a subagent report, a cron delivery) or names something that is not a
 * message id at all.
 */
export function questionForReplyTarget(replyToId: unknown): string | null {
  const id = typeof replyToId === "string" ? replyToId.trim() : "";
  if (!id) return null;
  const mapped = questionByEngineId.get(id)?.questionId ?? id;
  return UUID_RE.test(mapped) ? mapped : null;
}

export function recordReplyDelivered(questionId: string): void {
  const now = Date.now();
  forgetOld(repliedAt, (at) => at, now);
  repliedAt.set(questionId, now);
}

/** Whether a reply to this owner message went out on the outbound road at or after ``since``. */
export function replyDeliveredSince(questionId: string, since: number): boolean {
  return (repliedAt.get(questionId) ?? 0) >= since;
}

/** Test-only: forget every turn, id and reply. */
export function __resetOwnerTurns(): void {
  openTurns.clear();
  questionByEngineId.clear();
  repliedAt.clear();
}
