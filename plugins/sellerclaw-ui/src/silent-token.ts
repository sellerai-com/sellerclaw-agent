/**
 * The silent-reply contract, taken from the engine instead of re-implemented.
 *
 * ``NO_REPLY`` is not our invention: it is OpenClaw's own auto-reply token (``src/auto-reply/
 * tokens.ts``), and the engine ships a family of matchers for it — case-insensitive, tolerant of
 * repeats and edge punctuation, aware of a JSON-wrapped token and of one written after a block of
 * model reasoning. This plugin used to compare ``text.trim().toUpperCase() === "NO_REPLY"``
 * instead, the strictest reading possible, and every shape the engine would have recognised
 * reached the owner as an ordinary message (staging chat f0e2835a, 2026-09-04: a run that wrote
 * two sentences and then the token had both delivered, token included).
 *
 * ``isSilentReplyPayloadText`` is imported from ``plugin-sdk/reply-chunking``, which has exported
 * it since 2026.8.
 *
 * What the SDK does **not** export is ``stripSilentToken``, the engine's own answer to mixed
 * content, so the trailing-token strip is mirrored here.
 *
 * Mixed content — prose and then the token — is read differently on the two roads, because what
 * silence costs is different on each:
 *
 *  * A completion run (nobody asked it anything; a child finished) that ends on the token asked for
 *    silence, and the prose before it is the model talking to the plumbing: "already sent this",
 *    "nothing to add". Delivering that is noise. :func:`isSilentAnswer` therefore treats it as
 *    silence — the engine reads its own "I have nothing to add. NO_REPLY" shape the same way.
 *  * A live chat turn is the opposite: the owner just wrote something and is watching an empty
 *    chat. Answering nothing is the worst outcome there, so :func:`visibleAnswerText` keeps the
 *    prose and drops only the token. A reply that is *nothing but* the token is still silence.
 */
import { SILENT_REPLY_TOKEN, isSilentReplyPayloadText } from "openclaw/plugin-sdk/reply-chunking";

export { SILENT_REPLY_TOKEN };

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/**
 * A run of the token at the very end of the text, with the whitespace or bold markers before it.
 *
 * Shaped after the engine's ``getSilentTrailingRegex``: the token must start the string or follow
 * whitespace/asterisks, so a word ending in the token (``ANTI-NO_REPLY``) is left alone.
 */
const trailingSilentTokenRe = new RegExp(
  `(?:^|\\s+|\\*+)${escapeRegExp(SILENT_REPLY_TOKEN)}(?:\\s+${escapeRegExp(SILENT_REPLY_TOKEN)})*[\\s*]*$`,
  "i",
);

/**
 * What of this text the owner should see once the token is taken out of it.
 *
 * Two steps: the engine's own token-only contract (which covers the wrapped and reasoning-prefixed
 * shapes), then the trailing-token strip for text that carries something else as well. For the
 * road where the owner is waiting on an answer, this is the whole contract — the token never
 * reaches them, and neither does an empty reply where they wrote a question.
 */
export function visibleAnswerText(text: string): string {
  if (!text.trim()) return "";
  if (isSilentReplyPayloadText(text)) return "";
  return text.replace(trailingSilentTokenRe, "").trim();
}

/**
 * Whether the run asked to stay silent — the reading for a completion run, where nobody is waiting
 * on this particular turn.
 *
 * Ending on the token is the ask, whatever stands before it. That prose is written to the plumbing,
 * not to the owner ("already reported this", "nothing to add"), and putting it in the chat is how
 * a duplicate wake-up turned into a message nobody needed (staging chat f0e2835a, 2026-09-04).
 */
export function isSilentAnswer(text: string): boolean {
  if (!text.trim()) return true;
  if (isSilentReplyPayloadText(text)) return true;
  return trailingSilentTokenRe.test(text);
}

/** The prose a silent run wrote before its token — for the log line that records what was dropped. */
export function droppedSilentProse(text: string): string {
  return visibleAnswerText(text);
}
