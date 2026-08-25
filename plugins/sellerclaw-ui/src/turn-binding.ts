import { getSharedState } from "./shared-state.js";

/**
 * The assistant message a live dispatch offers to the first `message`-tool send of its turn.
 *
 * Since OpenClaw 2026.8 the supervisor usually answers with the `message` tool rather than a
 * reply the dispatcher hands us. Each outbound send minted its own cloud message id, so the
 * dispatch's own message — the one its streamed reasoning accumulates under — stayed empty and
 * was dropped at `turn/end`, taking the "Thought" panel with it, while the answer lived in a
 * second message. Binding the two to one id puts the thinking and the answer it explains in the
 * same message.
 *
 * The dispatch keeps ownership: it sends `turn/end` from `finishTurn`, so a send that claims the
 * id must not end the turn — a second end would finalize the message while the run is still
 * going. Claiming is one-shot (later sends in the turn are their own messages) and stops once the
 * dispatch streams content of its own.
 *
 * Kept in a process-wide slot (see `shared-state.ts`): the plugin module is re-evaluated on every
 * registry pass, and a binding written by the dispatch must still be there when the send arrives.
 */

/**
 * How long a binding survives without its dispatch closing it.
 *
 * A turn that outlives this was abandoned (the gateway died mid-run), and the cloud's stale-turn
 * safety net has long since finalized its message — offering that id to a later send would append
 * to a finished message.
 */
const BINDING_TTL_MS = 600_000;

export type TurnBinding = {
  messageId: string;
  chatId: string | null;
  /** True once the dispatch posted a part of its own — its message is no longer free to claim. */
  streamed: boolean;
  expiresAt: number;
};

const bindings = getSharedState("turn-binding:by-session", () => new Map<string, TurnBinding>());

/** The live binding for a session, dropping it when its window has passed. */
export function readTurnBinding(sessionKey: string): TurnBinding | null {
  const binding = bindings.get(sessionKey);
  if (!binding) return null;
  if (binding.expiresAt <= Date.now()) {
    bindings.delete(sessionKey);
    return null;
  }
  return binding;
}

/**
 * Register the dispatch's assistant message as this session's reasoning turn.
 *
 * Called at the start of every inbound turn, before anything is posted: an outbound `message`
 * send that happens while the dispatch has produced no parts of its own then lands in this same
 * message instead of opening a second one.
 */
export function bindInboundTurn(
  sessionKey: string,
  messageId: string,
  chatId: string | null,
): void {
  bindings.set(sessionKey, {
    messageId,
    chatId,
    streamed: false,
    expiresAt: Date.now() + BINDING_TTL_MS,
  });
}

/**
 * Mark the bound turn as carrying the dispatch's own streamed content.
 *
 * From here the message belongs to the streamed reply, so a `message`-tool send in the same turn
 * goes back to being its own message — appending it here would splice two separate utterances
 * into one bubble.
 */
export function markBoundTurnStreamed(sessionKey: string): void {
  const binding = readTurnBinding(sessionKey);
  if (binding) binding.streamed = true;
}

/** Drop the binding when its dispatch finishes, unless a newer turn already replaced it. */
export function releaseTurnBinding(sessionKey: string, messageId: string): void {
  const binding = bindings.get(sessionKey);
  if (binding && binding.messageId === messageId) bindings.delete(sessionKey);
}

/**
 * The dispatch's message id for an outbound send to deliver into, or `null` to mint a fresh one.
 *
 * Claiming consumes the binding: only the first send of a turn joins the thinking, later ones are
 * separate messages. The claimed turn is left open — its dispatch closes it.
 */
export function claimBoundTurnForSend(sessionKey: string): { messageId: string } | null {
  const binding = readTurnBinding(sessionKey);
  if (!binding || binding.streamed) return null;
  bindings.delete(sessionKey);
  return { messageId: binding.messageId };
}

/** Test-only: forget every binding so cases cannot leak into each other. */
export function __resetTurnBindings(): void {
  bindings.clear();
}
