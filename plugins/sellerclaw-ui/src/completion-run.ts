/**
 * Telling a completion (announce / requester-settle) run from a live chat turn.
 *
 * Shared by the two modules that must treat them differently: `completion-delivery.ts` corrects
 * what the runtime delivers for such a run, and `reasoning-relay.ts` reports its thinking (a live
 * chat turn streams its own through `replyOptions`, so relaying there would double it).
 */

/** Announce (subagent-completion) runs carry this prefix on their run id. */
export const ANNOUNCE_RUN_ID_PREFIX = "announce:";

/**
 * Fallback signal, used when a future runtime stops prefixing announce run ids: the opening
 * line OpenClaw writes for a completion event, in both its plain and protected framings.
 * Matched against the current turn's prompt only — the same text sitting in older history
 * must not turn a normal chat turn into a completion turn.
 */
const COMPLETION_TRIGGER_MARKERS = [
  "A background task completed.",
  "[Internal task completion event]",
];

function asString(value: unknown): string {
  return typeof value === "string" ? value : "";
}

/** Index of the last user-role message, or `-1`. That message opens the current run. */
export function lastUserMessageIndex(messages: unknown): number {
  if (!Array.isArray(messages)) return -1;
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index] as { role?: unknown } | null;
    if (message && typeof message === "object" && message.role === "user") return index;
  }
  return -1;
}

/**
 * Text of the last user-role message, which in a completion run is the trigger the runtime
 * just pushed. Scoped to the *last* one on purpose: the trigger of an earlier completion
 * stays in the transcript, and matching it again would misclassify a live chat turn.
 */
export function lastUserMessageText(messages: unknown): string {
  const index = lastUserMessageIndex(messages);
  if (index < 0 || !Array.isArray(messages)) return "";
  const content = (messages[index] as { content?: unknown }).content;
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  return content
    .map((block) =>
      block && typeof block === "object" ? asString((block as { text?: unknown }).text) : "",
    )
    .join("\n");
}

export function isCompletionRun(runId: unknown, messages: unknown): boolean {
  if (asString(runId).startsWith(ANNOUNCE_RUN_ID_PREFIX)) return true;
  // Must *open* the turn, not merely appear in it: an owner who pastes the trigger wording into
  // chat should not have their live turn rerouted through the completion path.
  const trigger = lastUserMessageText(messages).trimStart();
  return COMPLETION_TRIGGER_MARKERS.some((marker) => trigger.startsWith(marker));
}

/** The silent token: a run whose whole answer is this one word deliberately said nothing. */
export const SILENT_REPLY_TOKEN = "NO_REPLY";

/**
 * Whether this run declined to answer — its visible text is exactly the silent token.
 *
 * A duplicate completion event wakes the supervisor for a turn whose only conclusion is "I have
 * already answered this". That turn contributes nothing to what the owner reads, so its thinking
 * does not belong beside the answer: it is commentary on runtime plumbing (retried events, reply
 * sentinels), and shown in the Thought panel it displaces the reasoning that produced the answer.
 */
export function isSilentRun(messages: unknown): boolean {
  return lastAssistantText(messages).trim() === SILENT_REPLY_TOKEN;
}

/** Visible text of the run's last assistant message — its answer, thinking blocks excluded. */
export function lastAssistantText(messages: unknown): string {
  if (!Array.isArray(messages)) return "";
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index] as { role?: unknown; content?: unknown } | null;
    if (!message || typeof message !== "object" || message.role !== "assistant") continue;
    const content = message.content;
    if (typeof content === "string") return content;
    if (!Array.isArray(content)) return "";
    return content
      .map((block) => {
        if (!block || typeof block !== "object") return "";
        const part = block as { type?: unknown; text?: unknown };
        return part.type === "text" ? asString(part.text) : "";
      })
      .join("")
      .trim();
  }
  return "";
}
