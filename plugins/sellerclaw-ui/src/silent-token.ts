/**
 * The silent-reply contract: when the owner must not see what the agent wrote.
 *
 * ``NO_REPLY`` is not our invention: it is OpenClaw's own auto-reply token (``src/auto-reply/
 * tokens.ts``), and the token itself is still taken from the engine (``plugin-sdk/reply-runtime``).
 * This plugin used to compare ``text.trim().toUpperCase() === "NO_REPLY"``, the strictest reading
 * possible, and every other shape a model writes the token in reached the owner as an ordinary
 * message (staging chat f0e2835a, 2026-09-04: a run that wrote two sentences and then the token had
 * both delivered, token included).
 *
 * Until 2026.11.5 the shapes came from the engine's ``isSilentReplyPayloadText``
 * (``plugin-sdk/reply-chunking``): case-insensitive, tolerant of repeats and edge punctuation,
 * aware of a JSON-wrapped token and of one written after a block of model reasoning. 2026.11.5
 * removed it with no alias (``docs/plugins/sdk-migration/removed-surfaces.md``, "Reply silence
 * parsing") and narrowed the engine's own reading to an exact ``NO_REPLY``: punctuation, quotes,
 * JSON and prose are a visible answer there, and delivery plugins are told to forward prepared
 * text as it is. We keep the wider reading on purpose. The owner reads a business chat, not a
 * transcript, and a bare ``NO_REPLY.`` or ``{"action":"NO_REPLY"}`` in it is plumbing showing
 * through, never an answer — so :func:`isTokenOnlyReply` below is a port of the removed matcher,
 * kept to the shapes this plugin relies on (``__tests__/silent-token.test.ts``).
 *
 * The engine's ``stripSilentToken`` (its answer to mixed content) was never exported either, so the
 * trailing-token strip is mirrored here too.
 *
 * Mixed content — prose and then the token — is read differently on the two roads, because what
 * silence costs is different on each:
 *
 *  * A completion run (nobody asked it anything; a child finished) that ends on the token asked for
 *    silence, and the prose before it is the model talking to the plumbing: "already sent this",
 *    "nothing to add". Delivering that is noise. :func:`isSilentAnswer` therefore treats it as
 *    silence — the engine read its own "I have nothing to add. NO_REPLY" shape the same way until
 *    2026.11.5.
 *  * A live chat turn is the opposite: the owner just wrote something and is watching an empty
 *    chat. Answering nothing is the worst outcome there, so :func:`visibleAnswerText` keeps the
 *    prose and drops only the token. A reply that is *nothing but* the token is still silence.
 */
import { SILENT_REPLY_TOKEN } from "openclaw/plugin-sdk/reply-runtime";

export { SILENT_REPLY_TOKEN };

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

const escapedToken = escapeRegExp(SILENT_REPLY_TOKEN);

/** The token alone, any case, possibly repeated: ``NO_REPLY``, ``no_reply NO_REPLY``. */
const exactTokenRe = new RegExp(`^\\s*${escapedToken}(?:\\s+${escapedToken})*\\s*$`, "i");

/** One closed reasoning block at the very start: ``<thinking>…</thinking>``, ``<think>``, ``<thought>``. */
const taggedReasoningPrefixRe =
  /^\s*<\s*(?:[\w-]+:)?(?:think(?:ing)?|thought|antthinking)\b[^<>]*>[\s\S]*?<\s*\/\s*(?:[\w-]+:)?(?:think(?:ing)?|thought|antthinking)\s*>\s*/i;

/** A plain reasoning heading line at the very start: ``Thinking:`` / ``Reasoning`` and a newline. */
const plainReasoningPrefixRe = /^\s*(?:think(?:ing)?|thought|analysis|reasoning)\s*:?\s*\r?\n/i;

/** Punctuation (``**``, ``.``, ``!``, quotes) around the text, as the engine's ``stripEdgePunctuation``. */
function stripEdgePunctuation(text: string): string {
  return text.replace(/^\p{P}+/u, "").replace(/\p{P}+$/u, "");
}

/** ``"NO_REPLY"`` as a JSON string, or ``{"action": "NO_REPLY"}`` and nothing else. */
function isJsonWrappedToken(text: string): boolean {
  const looksWrapped =
    (text.startsWith('"') && text.endsWith('"')) || (text.startsWith("{") && text.endsWith("}"));
  if (!looksWrapped || !text.includes(SILENT_REPLY_TOKEN)) return false;
  try {
    const parsed: unknown = JSON.parse(text);
    if (typeof parsed === "string") return parsed.trim() === SILENT_REPLY_TOKEN;
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return false;
    const keys = Object.keys(parsed);
    const action = (parsed as { action?: unknown }).action;
    return keys.length === 1 && typeof action === "string" && action.trim() === SILENT_REPLY_TOKEN;
  } catch {
    return false;
  }
}

/**
 * Whether the text is nothing but the token, in any of the shapes a model writes it in: bare or
 * repeated, any case, wrapped in punctuation or bold, JSON-wrapped, or after a reasoning block.
 */
export function isTokenOnlyReply(text: string): boolean {
  const trimmed = text.trim();
  if (!trimmed) return false;
  if (exactTokenRe.test(trimmed) || exactTokenRe.test(stripEdgePunctuation(trimmed))) return true;
  if (isJsonWrappedToken(trimmed)) return true;
  for (const prefix of [taggedReasoningPrefixRe, plainReasoningPrefixRe]) {
    const rest = trimmed.replace(prefix, "");
    if (rest !== trimmed) return isTokenOnlyReply(rest);
  }
  return false;
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
 * Two steps: the token-only reading (:func:`isTokenOnlyReply`, which covers the wrapped and
 * reasoning-prefixed shapes), then the trailing-token strip for text that carries something else
 * as well. For the road where the owner is waiting on an answer, this is the whole contract — the
 * token never reaches them, and neither does an empty reply where they wrote a question.
 */
export function visibleAnswerText(text: string): string {
  if (!text.trim()) return "";
  if (isTokenOnlyReply(text)) return "";
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
  if (isTokenOnlyReply(text)) return true;
  return trailingSilentTokenRe.test(text);
}

/** The prose a silent run wrote before its token — for the log line that records what was dropped. */
export function droppedSilentProse(text: string): string {
  return visibleAnswerText(text);
}
