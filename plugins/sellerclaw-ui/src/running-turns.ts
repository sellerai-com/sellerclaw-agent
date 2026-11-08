/**
 * Turns whose dispatch is running, by chat: what reaches them when something else lands in that
 * chat while they are still writing.
 *
 * A turn opens its reply on its first visible words and writes into it until its run ends.
 * Anything published into the chat in the meantime used to sit *above* words the run wrote after
 * it: the answer to the owner's correction landed above the correction, a final "done" above the
 * agent's own "starting" line sent with the ``message`` tool (2026-11-08). So each of those tells
 * the chat's running turns, and they go on in a new reply below it.
 *
 * Kept in shared state: the send road can run in another instance of this module than the turn
 * it has to reach (see ``shared-state.ts``).
 */
import { getSharedState } from "./shared-state.js";

export interface RunningTurn {
  /**
   * Something landed in the chat: close the reply written so far and go on in a new one below it.
   * ``replyTo`` names the owner message the rest of the turn answers, when one does.
   */
  continueBelow: (replyTo?: string) => void;
}

function turnsByChat(): Map<string, Set<RunningTurn>> {
  return getSharedState("runningTurnsByChat", () => new Map<string, Set<RunningTurn>>());
}

const chatKey = (chatId: string): string => chatId.trim().toLowerCase();

/** Register a turn for as long as its dispatch runs; call the returned function when it settles. */
export function registerRunningTurn(chatId: string, turn: RunningTurn): () => void {
  const key = chatKey(chatId);
  const byChat = turnsByChat();
  const turns = byChat.get(key) ?? new Set<RunningTurn>();
  turns.add(turn);
  byChat.set(key, turns);
  return () => {
    turns.delete(turn);
    if (turns.size === 0 && byChat.get(key) === turns) byChat.delete(key);
  };
}

/**
 * Tell the chat's running turns that something landed in it just now.
 *
 * ``except`` is the turn that published it, when it is one of them; ``replyTo`` is the owner
 * message the turns answer from here on (the owner's note a run took mid-way).
 */
export function continueRunningTurnsBelow(
  chatId: string | null | undefined,
  options: { except?: RunningTurn; replyTo?: string } = {},
): void {
  if (!chatId) return;
  for (const turn of [...(turnsByChat().get(chatKey(chatId)) ?? [])]) {
    if (turn !== options.except) turn.continueBelow(options.replyTo);
  }
}
