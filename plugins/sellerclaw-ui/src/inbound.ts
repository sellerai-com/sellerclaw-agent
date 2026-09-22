import type { OpenClawPluginApi } from "openclaw/plugin-sdk/core";
import { dispatchInboundDirectDmWithReasoning } from "./inbound-reply-with-reasoning.js";
import { isReasoningReplyPayload } from "openclaw/plugin-sdk/reply-payload";
import { readJsonWebhookBodyOrReject } from "openclaw/plugin-sdk/webhook-ingress";
import { ensureRouteScopeGatewayResolver, SUPERVISOR_TURN_SCOPE_SURFACE } from "./gateway-scope.js";
import { saveMediaBuffer } from "openclaw/plugin-sdk/media-store";
import {
  abortAgentHarnessRun,
  resolveActiveEmbeddedRunSessionId,
} from "openclaw/plugin-sdk/agent-harness-runtime";

import { outboundDeliveredSince, resolveSellerclawUiAccount } from "./channel.js";
import { logDelivery, logError, logInfo, logWarn } from "./log.js";
import {
  abortOwnerTurns,
  closeOwnerTurn,
  openOwnerTurn,
  rememberEngineMessageId,
  replyDeliveredSince,
} from "./owner-turns.js";
import { visibleAnswerText } from "./silent-token.js";
import {
  claimContinuation,
  consumeOwnerAbort,
  isAdmissionRaceFailure,
  isHumanNeededFailure,
  isTransportTurnFailure,
  markOwnerAbort,
  MAX_CONTINUATIONS,
  readRunOutcome,
  resetContinuations,
} from "./run-outcome.js";
import {
  postScheduledTaskFeasibility,
  postScheduledTaskRun,
  postThought,
  postTurnEnd,
  postTurnPart,
  postTurnPreview,
  postTurnStart,
  resolveMediaKind,
  resolveOutboundMediaUrl,
  type ScwUiAccount,
} from "./send.js";
import { getRuntime } from "./runtime-store.js";

interface InboundPayload {
  chat_id: string;
  agent_id: string;
  user_id: string;
  text: string;
  message_id?: string;
  /** Multimodal parts mirroring SellerClaw persisted raw_content. */
  raw_content?: unknown[];
  /**
   * Catch-up re-delivery of a still-PROCESSING message (cloud restarted mid-turn, so the
   * turn result was lost and the row never left PROCESSING). When set, dispatch as a FRESH
   * OpenClaw turn (new MessageSid) so any session-level dedup on the original message id
   * can't suppress the re-run. The cloud message_id is still used to key the in-flight
   * guard so a redelivery that races a genuinely-running turn is dropped, not duplicated.
   */
  redelivery?: boolean;
  /**
   * User's configured agent effort ("medium" | "high" | "max") captured by the cloud at
   * send time. The same value is also embedded as a `[request-effort: …]` line at the top
   * of `text`, so the model sees it either way; this field is the structured copy for the
   * dispatch context (Effort). Absent/null on catch-up redeliveries and older clouds.
   */
  effort?: string | null;
}

/**
 * Cloud message ids currently being dispatched. The cloud re-sends every still-PROCESSING
 * message on each (re)connect (catch-up). If such a re-delivery lands while the original
 * turn is still running, dispatching again would produce a duplicate reply — so we drop it.
 * Keyed by the stable cloud message_id; cleared once the turn finishes (success OR failure).
 */
const inFlightInboundMessageIds = new Set<string>();

interface ImagePart {
  url: string;
  filename: string;
  contentType: string;
  fileId: string | null;
}

interface FilePart {
  url: string;
  filename: string;
  contentType: string;
  fileId: string | null;
}

function asStringField(value: unknown): string {
  return typeof value === "string" && value.trim() ? value.trim() : "";
}

/**
 * Extract the storage ``file_id`` from a SellerClaw download URL.
 *
 * The backend serves files at ``.../files/<file_id>/<filename>``. The base prefix in
 * front of ``/files/`` is empty in tests and ``/agent`` in staging/prod, so we find the
 * ``files`` segment anywhere in the path and take the next segment as the file_id.
 * Used as a fallback when the structured ``file_id`` field is missing (older messages
 * persisted before the field was added).
 */
function extractFileIdFromUrl(url: string): string | null {
  try {
    const segments = new URL(url).pathname.split("/").filter(Boolean);
    for (let i = 0; i < segments.length - 2; i += 1) {
      if (segments[i] === "files") return segments[i + 1] || null;
    }
    return null;
  } catch {
    return null;
  }
}

function extractAttachmentParts(parts: unknown): { images: ImagePart[]; files: FilePart[] } {
  const images: ImagePart[] = [];
  const files: FilePart[] = [];
  if (!Array.isArray(parts)) return { images, files };
  for (const part of parts) {
    if (!part || typeof part !== "object") continue;
    const p = part as Record<string, unknown>;
    const filename = asStringField(p.filename);
    const contentType = asStringField(p.content_type);
    const explicitFileId = asStringField(p.file_id) || null;
    if (p.type === "image_url" && p.image_url && typeof p.image_url === "object") {
      const url = asStringField((p.image_url as { url?: unknown }).url);
      if (url) {
        const fileId = explicitFileId ?? extractFileIdFromUrl(url);
        images.push({ url, filename, contentType, fileId });
      }
      continue;
    }
    if (p.type === "file_url" && p.file_url && typeof p.file_url === "object") {
      const url = asStringField((p.file_url as { url?: unknown }).url);
      if (url) {
        const fileId = explicitFileId ?? extractFileIdFromUrl(url);
        files.push({ url, filename, contentType, fileId });
      }
    }
  }
  return { images, files };
}

/**
 * Rewrites the origin of a SellerClaw-side download URL to ``account.apiBaseUrl``.
 *
 * Backend persists URLs built from ``FILE_STORAGE_BASE_URL`` (often a user-facing
 * value like ``http://localhost:8000``). Inside the OpenClaw container that origin
 * is unreachable; the plugin must hit the cloud API at ``apiBaseUrl`` instead.
 */
function rewriteUrlHost(url: string, apiBaseUrl: string): string {
  try {
    const target = new URL(url);
    const base = new URL(apiBaseUrl);
    target.protocol = base.protocol;
    target.hostname = base.hostname;
    target.port = base.port;
    return target.toString();
  } catch {
    return url;
  }
}

function filenameFromUrl(url: string, fallback: string): string {
  try {
    const parsed = new URL(url);
    const last = parsed.pathname.split("/").filter(Boolean).pop();
    if (last) return decodeURIComponent(last);
  } catch {
    /* fallthrough */
  }
  return fallback || "attachment";
}

const ATTACHMENT_FETCH_TIMEOUT_MS = 30_000;

async function downloadAttachment(
  url: string,
  agentApiKey: string,
): Promise<{ buffer: Buffer; contentType: string }> {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), ATTACHMENT_FETCH_TIMEOUT_MS);
  try {
    const response = await fetch(url, {
      headers: { Authorization: `Bearer ${agentApiKey}` },
      signal: ctl.signal,
    });
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    const arrayBuffer = await response.arrayBuffer();
    const contentType = response.headers.get("content-type") ?? "application/octet-stream";
    return { buffer: Buffer.from(arrayBuffer), contentType };
  } finally {
    clearTimeout(timer);
  }
}

type AttachmentKind = "image" | "file";

/**
 * Download an attachment from SellerClaw, persist it in the OpenClaw media
 * store, and return a runtime-recognized marker.
 *
 * For images we keep the legacy ``[Image: source: <abs>]`` marker — the
 * runtime's ``MESSAGE_IMAGE_PATTERN`` picks it up and loads the file as a
 * vision block in the user prompt.
 *
 * For non-image files we emit a structured ``[Attachment: file_id=<id>
 * local=<abs>]`` marker so the agent can call into ``sellerclaw spreadsheet``
 * / ``sellerclaw files`` HTTP endpoints by ``file_id`` directly, without
 * re-uploading. The local path is kept on the same line for legacy tools
 * (``Read`` / ``Bash``) that still operate on disk. When ``file_id`` is
 * missing (old messages persisted before the field was added) we fall back
 * to the legacy ``MEDIA:`` marker.
 *
 * We deliberately do not use ``media://inbound/<id>``: that claim-check URI
 * only resolves when the Gateway pre-registers the id in ``attachmentUris``,
 * which channel plugins don't have access to.
 */
async function persistAttachmentMarker(
  api: OpenClawPluginApi,
  account: ScwUiAccount,
  part: ImagePart | FilePart,
  kind: AttachmentKind,
): Promise<string> {
  const rewritten = rewriteUrlHost(part.url, account.apiBaseUrl);
  const fallbackLabel = kind === "image" ? "image" : "file";
  const displayName = part.filename || filenameFromUrl(rewritten, fallbackLabel);
  try {
    const { buffer, contentType } = await downloadAttachment(rewritten, account.agentApiKey);
    const finalContentType = part.contentType || contentType;
    const saved = await saveMediaBuffer(buffer, finalContentType, "inbound", undefined, displayName);
    if (kind === "image") {
      return `[Image: source: ${saved.path}]`;
    }
    if (part.fileId) {
      return `${displayName}\n[Attachment: file_id=${part.fileId} local=${saved.path}]`;
    }
    return `${displayName}\nMEDIA: \`${saved.path}\``;
  } catch (err) {
    logWarn(
      api,
      `sellerclaw-ui: ${kind} attachment fetch/save failed for ${displayName}: ${String(err)}`,
    );
    return `[attachment unavailable: ${displayName}]`;
  }
}

async function materializeAttachmentsForAgent(
  api: OpenClawPluginApi,
  account: ScwUiAccount,
  rawContent: unknown,
): Promise<string[]> {
  const { images, files } = extractAttachmentParts(rawContent);
  if (images.length === 0 && files.length === 0) return [];
  const imageMarkers = await Promise.all(
    images.map((img) => persistAttachmentMarker(api, account, img, "image")),
  );
  const fileMarkers = await Promise.all(
    files.map((f) => persistAttachmentMarker(api, account, f, "file")),
  );
  return [...imageMarkers, ...fileMarkers];
}

/**
 * Block-starting markdown patterns whose presence at the start of a new
 * delta means the previous delta MUST be terminated by a paragraph break
 * (`\n\n`), otherwise the structure collapses:
 *   - ATX headings (`# `, `## `, ...)
 *   - fenced code blocks (` ``` `)
 *   - blockquotes (`> `)
 *   - bullet/ordered lists (`- `, `* `, `+ `, `1. `, `1) `)
 *   - thematic breaks (`---`, `***`, `___`)
 *   - GFM table rows (`|`) — opening a table after prose. A row following another
 *     row is handled earlier, by ``TABLE_LINE_RE``: inside a table a blank line is
 *     a terminator, not a separator.
 */
const MARKDOWN_BLOCK_START_RE =
  /^(?:#{1,6}\s|```|>(?:\s|$)|---|\*\*\*|___|\||[-*+]\s|\d+[.)]\s)/;

/** ATX heading at line start (`# `, `## `, ..., up to `###### `). */
const ATX_HEADING_RE = /^#{1,6}\s/;

/**
 * A GFM table line — a row or the `| --- | --- |` delimiter — recognised by the leading
 * pipe. Deliberately not matched on a pipe anywhere in the line: prose says "A | B" often
 * enough, and starting a line with `|` is what actually makes it part of a table.
 */
const TABLE_LINE_RE = /^\s*\|/;

/**
 * Picks a joiner string to insert between two consecutive streaming deltas.
 *
 * This shapes the LIVE PREVIEW only — the throwaway render of a reply while the
 * model is still writing it, plus the copy an interrupted turn falls back on. The
 * reply we persist is the model's own final text, which needs no joining. Keep the
 * distinction: guesswork is fine for something the final text replaces seconds
 * later, and was never fine for the message itself (a cut inside a table used to
 * leave the report's rows rendered as plain text — that is what moved the persisted
 * road off this function).
 *
 * The OpenClaw block-streaming chunker cuts the assistant reply at internal
 * boundaries and drops the whitespace it cut on. We can't see what was
 * dropped, so we use heuristics on the visible content at the boundary:
 *
 *   * Table row on BOTH sides of the boundary → exactly one `\n`. We are inside
 *     a table, where a blank line does not separate rows, it ends the table —
 *     leaving every remaining row rendered as literal `| … |` text. This is the
 *     one rule that counts the whitespace already present at the boundary,
 *     because it is the one place a spare newline is not harmless.
 *   * Markdown block-starter at the head of the next delta → `\n\n`. Without
 *     this the heading / list / fence would be absorbed into the previous
 *     paragraph (or worse, a fence would never close). Reached for a table only
 *     when the previous delta was NOT a table line — i.e. a table opening after
 *     prose, which does want the break.
 *   * Closing code fence at the tail of the previous delta → `\n\n`. The
 *     fence has to live on a line of its own.
 *   * Previous delta's last line is itself an ATX heading (e.g. `## Title`
 *     with no trailing newline) → `\n\n`. Headings are line-bounded; without
 *     a newline the next chunk's text would parse as part of the heading.
 *   * Otherwise → a single space. We assume the chunker dropped whitespace
 *     inside running text. This may merge two paragraphs of regular prose
 *     into one (when the chunker cut at `\n\n` between plain paragraphs),
 *     which is a cosmetic loss; in exchange we never inject a paragraph
 *     break in the middle of a sentence, which is the visually disastrous
 *     failure mode.
 *
 * Note we deliberately do NOT escalate when the previous delta's last line
 * starts with a list / quote marker. Those structures can span an arbitrary
 * amount of content, and the chunker much more often cuts inside a long list
 * item than right after one. Defaulting to a space there keeps mid-item
 * continuations intact at the price of an occasional missed visual paragraph
 * break. The same reasoning covers a table row followed by prose: it is either
 * a table ending or a row cut mid-cell, we cannot tell, and gluing prose into
 * the last cell is the milder of the two ways to be wrong.
 *
 * Exported for unit-testing without spinning up the HTTP route.
 */
export function pickDeltaJoin(prevTail: string, nextHead: string): string {
  if (prevTail === "") return "";
  const tail = prevTail.replace(/\s+$/, "");
  const head = nextHead.replace(/^\s+/, "");
  if (tail === "" || head === "") return "";
  const lastNl = tail.lastIndexOf("\n");
  const tailLastLine = lastNl === -1 ? tail : tail.slice(lastNl + 1);
  if (TABLE_LINE_RE.test(head) && TABLE_LINE_RE.test(tailLastLine)) {
    // The caller prepends this joiner to the RAW delta, so whitespace the chunker did keep
    // still counts. One newline continues the table; two would end it.
    const boundary =
      prevTail.slice(tail.length) + nextHead.slice(0, nextHead.length - head.length);
    return boundary.includes("\n") ? "" : "\n";
  }
  if (MARKDOWN_BLOCK_START_RE.test(head)) return "\n\n";
  if (/```$/.test(tail)) return "\n\n";
  if (ATX_HEADING_RE.test(tailLastLine)) return "\n\n";
  return " ";
}

/**
 * Extract the deliverable fields from an OpenClaw outbound reply payload.
 *
 * The runtime normalizes every `deliver` payload to `{ text, mediaUrls,
 * mediaUrl, ... }` (see `createNormalizedOutboundDeliverer`). `MEDIA:` reply
 * directives and markdown images are surfaced here as `mediaUrls` / the legacy
 * single `mediaUrl`; we merge both into one deduped list.
 *
 * The silent token is stripped here because this is the one seam every delivered payload passes
 * through — a live chat turn, a scheduled run's summary, a resumed turn. It is a control word, not
 * content: an answer that is only the token means "say nothing", and a token trailing a real
 * answer is a habit of the model, not something the owner should read. Without this the word
 * reached owners verbatim (three staging chats), and the announce path's own check never saw those
 * turns at all — it only ever guarded the completion road. See ``silent-token.ts``.
 *
 * Exported for unit-testing without spinning up the HTTP route.
 */
export function readDeliverPayload(raw: unknown): {
  text: string;
  mediaUrls: string[];
  isError: boolean;
} {
  if (!raw || typeof raw !== "object") return { text: "", mediaUrls: [], isError: false };
  const p = raw as Record<string, unknown>;
  const rawText = typeof p.text === "string" ? p.text : "";
  // An engine failure notice is never the agent's own wording, so it keeps its text untouched;
  // ``finishTurn`` is what decides whether the owner sees anything about it.
  const text = p.isError === true ? rawText : visibleAnswerText(rawText);
  const isError = p.isError === true;
  const mediaUrls: string[] = [];
  const push = (value: unknown) => {
    if (typeof value !== "string") return;
    const trimmed = value.trim();
    if (trimmed && !mediaUrls.includes(trimmed)) mediaUrls.push(trimmed);
  };
  if (Array.isArray(p.mediaUrls)) {
    for (const entry of p.mediaUrls) push(entry);
  }
  push(p.mediaUrl);
  return { text, mediaUrls, isError };
}

/**
 * Prompt that resumes a turn the run budget cut short.
 *
 * Dispatched into the same (file-backed) session, so the agent still has its own context and can
 * see what it already finished — the automated form of the owner asking "что случилось?", which
 * is what actually rescued the 2026-08-19 incident.
 */
const CONTINUATION_PROMPT =
  "[internal] Your previous turn hit the per-turn time limit and was cut off before it could " +
  "reply. Continue from where you stopped: check what already completed before redoing " +
  "anything, finish what is left, and report the result to the owner. The interruption was " +
  "internal plumbing — no need to apologise for it or explain it.";

/**
 * Prompt that re-asks a turn which left the owner's message without a reply.
 *
 * The run ended by the model's own choice — on the silent token, on a yield, on a failed tool call —
 * and nothing reached the chat. The model reasons its way into that ending ("I already sent them the
 * status"), from text it wrote but the owner never received, so the prompt states the one fact it
 * could not see: nothing arrived.
 */
export const UNANSWERED_PROMPT =
  "[internal] Your last turn ended without a reply reaching the owner — nothing you wrote in it " +
  "was delivered. Answer their last message now, as the final text of this turn. If work is " +
  "still running, say what is running and that the result will land here. Don't mention this note.";

/**
 * One of our own prompts, marked the way the owner's messages are.
 *
 * The supervisor tells live chat — where the turn must end with a reply to the owner — from its
 * background runs by the ``[request-effort: …]`` line the cloud puts on every owner message. A
 * prompt of ours in the same chat without it matches none of the markers the agent knows, and is
 * open to being read as a background event that owes nobody an answer.
 */
export function asLiveChatPrompt(prompt: string, effort: string | null | undefined): string {
  return effort ? `[request-effort: ${effort}]\n${prompt}` : prompt;
}

/**
 * Whether the engine handed the message to a run already going in the session.
 *
 * In the ``steer`` queue mode a message that arrives mid-run is injected into that run when it can
 * take it; the dispatch then returns at once, and the answer is delivered through the running
 * turn. The engine reports this on its turn result (``dispatchResult.deferredToActiveRun``), and
 * uses the same field to keep such a message out of its own "dispatched with no reply" warning.
 */
export function deferredToActiveRun(turnResult: unknown): "steer" | "followup" | null {
  if (!turnResult || typeof turnResult !== "object") return null;
  const dispatchResult = (turnResult as { dispatchResult?: unknown }).dispatchResult;
  if (!dispatchResult || typeof dispatchResult !== "object") return null;
  const mode = (dispatchResult as { deferredToActiveRun?: unknown }).deferredToActiveRun;
  return mode === "steer" || mode === "followup" ? mode : null;
}

/**
 * Re-dispatches allowed for one message the runtime refused to admit (see
 * {@link isAdmissionRaceFailure}). Two, because the race is with a state change that is already
 * landing: if the message is still not admitted after two waits, something other than timing is
 * wrong and the owner should be told rather than kept waiting.
 */
const MAX_ADMISSION_REDISPATCHES = 2;

/**
 * Base wait before re-dispatching, multiplied by the attempt number.
 *
 * Long enough for the run that held the session to finish settling, short enough that the owner
 * reads it as the assistant thinking rather than as a stall.
 */
const ADMISSION_REDISPATCH_DELAY_MS = 750;

/**
 * Longest a turn keeps the owner's message pending while it waits in the engine's queue.
 *
 * The engine settles every queued message it holds — run, dropped or cancelled — so this is a
 * backstop for a lifecycle the engine lost, not a timeout anyone should reach: long enough for a
 * subagent report being written ahead of it, short of leaving the turn open for good.
 */
const QUEUED_TURN_MAX_WAIT_MS = 60 * 60_000;

/**
 * Run one chat turn: dispatch it to the agent, stream its parts to the cloud, finalize it, and —
 * when the run budget cut it short — resume it.
 *
 * Extracted from the inbound route so a self-recovery continuation can re-enter the same path
 * (``continuationAttempt`` marks those runs and bounds them). Everything above this call is HTTP
 * concern; everything here is the turn.
 */
async function startInboundTurn(params: {
  api: OpenClawPluginApi;
  account: ScwUiAccount;
  runtime: ReturnType<typeof getRuntime>;
  payload: InboundPayload;
  sessionKey: string;
  /** Cloud message id held in the in-flight guard; empty for a continuation, which holds none. */
  inboundMessageId: string;
  /** 1-based attempt number when this turn is itself a self-recovery continuation. */
  continuationAttempt?: number;
  /** Set once the chain has re-asked an unanswered message — it is re-asked at most once. */
  unansweredReasked?: boolean;
  /**
   * The assistant message a re-ask carries on, when the turn it follows never opened one. Its
   * thoughts are already streaming under this id, so ``thoughtSeqStart`` continues their count:
   * the cloud and the chat both drop a thought whose number they have seen for the message.
   */
  partsMessageId?: string;
  thoughtSeqStart?: number;
  /**
   * Cloud id of the owner message this turn answers. Our own follow-ups (a continuation, a re-ask)
   * carry the original's: they finish its answer, and dispatch without an id of their own.
   */
  questionId?: string;
}): Promise<void> {
  const { api, account, runtime, payload, sessionKey, inboundMessageId } = params;
  const questionId = params.questionId ?? (payload.message_id?.trim() || "");
  /**
   * This turn among the chat's open ones: what an owner's stop aborts, and what the engine is
   * handed so it drops the message wherever it is still waiting — in its queue, or parked for
   * the next step of the run it is to be added to.
   */
  const ownerTurn = openOwnerTurn(payload.chat_id, questionId);
  // One streaming assistant message per turn, started lazily on the first delivered
  // part (or eagerly at finish for an empty dispatch) and finalized after dispatch.
  const partsMessageId = params.partsMessageId ?? crypto.randomUUID();
  // Monotonic counter for thought stream parts (per assistant message). Frontend dedupes by seq.
  let thoughtSeq = params.thoughtSeqStart ?? 0;
  /**
   * Delivery timeline for this turn, logged at warn so it survives ``logging.level: warn``
   * and reaches the shipped logs.
   *
   * A turn that runs for minutes while the owner watches an empty chat is the symptom we
   * cannot currently explain: the engine is configured to stream (``blockStreamingDefault:
   * on``, ``blockStreamingBreak: text_end``, a 200-char floor), yet the assistant message
   * is created at the exact moment the run ends — so nothing arrived here before the final.
   * This records what the dispatcher actually handed us and when, which tells us whether
   * blocks are never emitted, emitted but empty, or emitted and dropped on our side.
   */
  const turnStartedAt = Date.now();
  let deliveryCount = 0;
  let partsTurnStarted = false;
  /** Set when the engine handed us a failure notice instead of an answer (see ``deliver``). */
  let sawErrorFinal = false;
  /** How much engine error text was kept out of the chat, for the delivery timeline. */
  let suppressedErrorChars = 0;
  /**
   * The engine's own wording of the failure, kept for {@link classifyTurnFailure}.
   *
   * It is the only description of *why* the run died that reaches this turn: on the deployed
   * runtime a gateway-scope plugin takes no part in the agent run's hooks, so ``agent_end``
   * (and with it {@link readRunOutcome}) never fires for us.
   */
  let lastErrorFinalText = "";
  /**
   * Whether anything real reached the chat this turn (committed text, preview delta, media).
   * Separates "the run succeeded and the error payload was just a tool-failure warning riding
   * along with the answer" from "the run 'succeeded' but the error text was all it produced"
   * (e.g. a mid-turn rate limit after tool calls) — the first must not read as a failure.
   */
  let postedAnyContent = false;
  /** Attempt number of the continuation ``finishTurn`` decided to start, if any. */
  let pendingContinuationAttempt: number | null = null;
  /** Set when ``finishTurn`` decided the owner's message has to be asked again. */
  let pendingUnansweredReask = false;
  /**
   * Set when the engine handed this message to a run already going in the session instead of
   * starting one for it. The answer is then delivered by that run's turn — this dispatch sees none
   * of it, and its silence is not the agent's.
   */
  let deferredTo: "steer" | "followup" | null = null;
  /**
   * Set when the re-ask carries on in this turn's assistant message instead of closing it.
   *
   * Possible only while the turn never opened one — nothing reached the chat, so there is nothing
   * to finalize. The owner's message stays pending, the chat keeps showing the agent at work, and
   * the re-asked reply lands as the answer to that message: closed and re-opened, it would arrive
   * as an unprompted update, with the owner's question already marked answered by nothing.
   */
  let heldOpenForReask = false;
  const ensurePartsTurn = async (): Promise<void> => {
    if (partsTurnStarted) return;
    partsTurnStarted = true;
    try {
      await postTurnStart(
        account,
        sessionKey,
        partsMessageId,
        payload.chat_id,
        questionId ? { replyTo: questionId } : undefined,
      );
    } catch (err) {
      partsTurnStarted = false;
      logError(api, `sellerclaw-ui: turn-start failed session_key=${sessionKey}: ${String(err)}`);
    }
  };

  /**
   * Hand this turn to the agent.
   *
   * A function rather than a bare IIFE so a dispatch the runtime refused *before admitting the
   * turn* can simply be repeated — see {@link isAdmissionRaceFailure} and the retry below.
   * ``redispatchAttempt`` is 0 for the first try; a retry re-runs everything here, attachments
   * included (they are materialized into the agent's own workspace, so re-materializing costs an
   * upload, not a duplicate in the chat).
   */
  const runDispatch = async (redispatchAttempt: number): Promise<void> => {
    const attachmentMarkers = await materializeAttachmentsForAgent(
      api,
      account,
      payload.raw_content ?? [],
    );
    const rawBody = [payload.text.trim(), ...attachmentMarkers]
      .filter((part) => part && part.length > 0)
      .join("\n");

    // Tail of the previously delivered preview delta. `pickDeltaJoin` needs
    // enough suffix to recognise both a closing code fence and the full
    // last line (for the ATX-heading check). 512 chars comfortably covers
    // any realistic heading / fence while keeping per-session memory
    // bounded.
    let prevTail = "";
    const TAIL_KEEP_CHARS = 512;
    // Which sub-message the preview deltas so far belong to. A turn can hold several ("working
    // on it…", then the answer), and `pickDeltaJoin` reads the gap between two of them as a
    // chunk the chunker cut mid-sentence — so it glues them with a single space and the status
    // note runs into the answer as one paragraph. The dispatcher labels every delivery with the
    // sub-message it belongs to, so that boundary is known rather than guessed. `null` until the
    // first block, and again after a final commits (which drops the preview buffer).
    let previewMessageIndex: number | null = null;
    // Whether this turn has already committed a sub-message, so the next one is separated
    // from it by a blank line. Finals are whole sub-messages, so unlike preview deltas there
    // is nothing to guess about the boundary between them.
    let committedAnyText = false;
    // Source paths/URLs already delivered as media this turn (same reason).
    const sentMedia = new Set<string>();
    const thoughtAgentId = payload.agent_id || "supervisor";

    // Reasoning ("thinking") stream → transient /thought channel. OpenClaw streams the
    // running CUMULATIVE reasoning text via ``replyOptions.onReasoningStream`` (NOT via the
    // ``deliver`` reply channel) and closes each reasoning block with ``onReasoningEnd``. We
    // diff the cumulative to a delta, accumulate it, and post one thought item per closed
    // block. The frontend renders these as the collapsible "Thinking…" panel; nothing is
    // persisted. ``reasoningPrior`` tracks the engine's cumulative (never reset mid-turn so
    // cross-block diffing stays correct); ``reasoningBuf`` holds deltas since the last flush.
    let reasoningPrior = "";
    let reasoningBuf = "";
    const REASONING_FLUSH_AT = 2000;
    const postThoughtText = (raw: string): void => {
      const text = raw.trim();
      if (!text) return;
      const seq = thoughtSeq++;
      void postThought(account, sessionKey, payload.chat_id, {
        message_id: partsMessageId,
        agent_id: thoughtAgentId,
        kind: "text",
        text,
        seq,
      }).catch((err) => {
        logError(
          api,
          `sellerclaw-ui: thought post failed session_key=${sessionKey}: ${String(err)}`,
        );
      });
    };
    const onReasoningStream = (evt: { text?: string }): void => {
      const t = (evt?.text ?? "").trim();
      if (!t || t === reasoningPrior) return;
      reasoningBuf += t.startsWith(reasoningPrior) ? t.slice(reasoningPrior.length) : t;
      reasoningPrior = t;
      // Bound a single item's size if a block stays open unusually long; cut on a line
      // boundary so we don't split mid-sentence.
      if (reasoningBuf.length >= REASONING_FLUSH_AT) {
        const nl = reasoningBuf.lastIndexOf("\n");
        const cut = nl > 0 ? nl : reasoningBuf.length;
        postThoughtText(reasoningBuf.slice(0, cut));
        reasoningBuf = reasoningBuf.slice(cut);
      }
    };
    const onReasoningEnd = (): void => {
      const rest = reasoningBuf;
      reasoningBuf = "";
      postThoughtText(rest);
    };

    // A catch-up re-delivery dispatches as a fresh OpenClaw turn: a new MessageSid so any
    // session-level dedup on the original id can't suppress the re-run. A retry after a refused
    // admission needs the same treatment, and for the same reason. The engine hands this id back
    // as the reply target of the run's sends, so it is mapped to the owner's message.
    const engineMessageId =
      payload.redelivery || redispatchAttempt > 0
        ? crypto.randomUUID()
        : (payload.message_id ?? crypto.randomUUID());
    rememberEngineMessageId(engineMessageId, questionId);
    // Settled by the engine once a message it queued has run, been dropped or been cancelled.
    let settleQueued: () => void = () => {};
    const queuedSettled = new Promise<void>((resolve) => {
      settleQueued = resolve;
    });
    const turnResult = await dispatchInboundDirectDmWithReasoning({
      cfg: api.config,
      runtime,
      channel: "sellerclaw-ui",
      channelLabel: "SellerClaw UI",
      accountId: "default",
      peer: { kind: "direct", id: payload.chat_id },
      senderId: payload.user_id,
      senderAddress: `sellerclaw-ui:${payload.user_id}`,
      recipientAddress: `sellerclaw-ui:direct:${payload.chat_id}`,
      conversationLabel: payload.chat_id,
      rawBody,
      messageId: engineMessageId,
      timestamp: Date.now(),
      commandAuthorized: true,
      // Structured copy of the per-message effort level (also present as a
      // `[request-effort: …]` line inside rawBody).
      extraContext: payload.effort ? { Effort: payload.effort } : undefined,
      // Reasoning stream → "Thinking…" panel. OpenClaw forwards these from ``replyOptions``
      // into the run (``onReasoningStream``/``onReasoningEnd``); without them the agent's
      // reasoning is produced but never reaches the chat.
      //
      // The lifecycle is how the engine reports a message it could not add to the run already
      // going and queued to run after it: settled once that is over. Without one the engine also
      // detaches a queued message from the turn's abort signal, and a stop would not reach it.
      replyOptions: {
        onReasoningStream,
        onReasoningEnd,
        abortSignal: ownerTurn.abort.signal,
        turnAdoptionLifecycle: {
          admission: "cancel-only",
          abortSignal: ownerTurn.abort.signal,
          onAdopted: () => {},
          onSettled: () => settleQueued(),
        },
      },
      deliver: async (
        replyPayload: unknown,
        dispatchInfo?: { kind?: string; assistantMessageIndex?: number },
      ) => {
        const { text, mediaUrls, isError } = readDeliverPayload(replyPayload);
        deliveryCount += 1;
        logDelivery(
          api,
          `inbound delivery #${deliveryCount} kind=${dispatchInfo?.kind ?? "none"} ` +
            `at=+${Math.round((Date.now() - turnStartedAt) / 1000)}s chars=${text.length} ` +
            `media=${mediaUrls.length} idx=${dispatchInfo?.assistantMessageIndex ?? "-"} ` +
            `${isError ? "isError=true " : ""}session_key=${sessionKey}`,
        );

        // An engine-synthesized failure notice, not the agent's answer. OpenClaw converts a
        // terminal run state (run-budget timeout, provider failure) into reply text and marks
        // it ``isError`` — posting that as a chat part is how "LLM request timed out." came to
        // be stored as an assistant reply. Hold it back; ``finishTurn`` decides what the owner
        // should see instead, and whether the turn resumes itself.
        if (isError) {
          sawErrorFinal = true;
          suppressedErrorChars += text.length;
          if (text.trim()) lastErrorFinalText = text;
          return;
        }

        // Reasoning blocks travel on a separate transient channel and are NOT
        // appended as user-visible parts. The UI renders them as a collapsible
        // "Thinking…" panel. ``isReasoningReplyPayload`` checks both the
        // ``isReasoning`` flag and the legacy ``reasoning:`` / ``thinking…``
        // text prefix used by the block-reply-pipeline.
        if (
          typeof replyPayload === "object" &&
          replyPayload !== null &&
          isReasoningReplyPayload(replyPayload as Record<string, unknown>) &&
          text.trim()
        ) {
          try {
            await postThought(account, sessionKey, payload.chat_id, {
              message_id: partsMessageId,
              agent_id: thoughtAgentId,
              kind: "text",
              text,
              seq: thoughtSeq++,
            });
          } catch (err) {
            logError(
              api,
              `sellerclaw-ui: thought post failed session_key=${sessionKey}: ${String(err)}`,
            );
          }
          return;
        }

        // One ordered turn per dispatch: media and text become ordered parts.
        // Media keeps its position relative to the streamed text (no separate
        // road), and the streamed text is never orphaned by an out-of-band send.
        for (const rawUrl of mediaUrls) {
          if (sentMedia.has(rawUrl)) continue;
          sentMedia.add(rawUrl);
          try {
            const { url, contentType } = await resolveOutboundMediaUrl(account, rawUrl);
            await ensurePartsTurn();
            await postTurnPart(
              account,
              sessionKey,
              partsMessageId,
              {
                part_id: crypto.randomUUID(),
                kind: resolveMediaKind(url, contentType),
                url,
                ...(contentType ? { content_type: contentType } : {}),
              },
              payload.chat_id,
            );
            postedAnyContent = true;
          } catch (err) {
            logError(
              api,
              `sellerclaw-ui: media part failed source=${rawUrl} session_key=${sessionKey}: ${String(err)}`,
            );
          }
        }
        if (text.trim()) {
          // The engine streams a long reply block-by-block (``kind === "block"``) and then
          // delivers the same sub-message once more, whole, as the model wrote it
          // (``kind === "final"``). The two are not interchangeable: a block arrives with the
          // whitespace the chunker cut on already dropped, so blocks re-glued are only
          // approximately formatted, while the final carries the model's exact wording.
          //
          // So blocks feed the live preview — shown, then thrown away — and the final is what
          // we persist. A short reply that never streamed still arrives as a final, so it
          // takes the same road and needs no special case.
          if (dispatchInfo?.kind === "block") {
            // A preview that outlives its turn is what the user keeps: `end_turn` persists it
            // when no final ever arrived. So the boundary between sub-messages has to be right
            // here too, not only on the committed road below.
            const messageIndex = dispatchInfo.assistantMessageIndex ?? null;
            const startsNewSubMessage =
              previewMessageIndex !== null && messageIndex !== previewMessageIndex;
            previewMessageIndex = messageIndex;
            const joiner = startsNewSubMessage ? "\n\n" : pickDeltaJoin(prevTail, text);
            const outText = joiner + text;
            prevTail = outText.slice(-TAIL_KEEP_CHARS);
            try {
              await ensurePartsTurn();
              await postTurnPreview(account, sessionKey, partsMessageId, payload.chat_id, {
                part_id: crypto.randomUUID(),
                text: outText,
              });
              // Counts as content: the cloud persists an orphaned preview tail at turn end,
              // so text the owner watched appear is real even if no final ever commits it.
              postedAnyContent = true;
            } catch (err) {
              // A dropped preview delta costs a moment of live rendering, never the reply:
              // the final still lands, and an interrupted turn keeps whatever preview the
              // cloud did receive.
              logError(
                api,
                `sellerclaw-ui: preview delta failed session_key=${sessionKey}: ${String(err)}`,
              );
            }
          } else {
            // Separate consecutive sub-messages (e.g. a "working on it…" status, then the
            // answer) — each final is a complete message, so the boundary is known, not
            // guessed.
            const outText = committedAnyText ? `\n\n${text}` : text;
            prevTail = "";
            // Committing text drops the preview buffer cloud-side, so the next block starts a
            // fresh preview with nothing before it to be separated from.
            previewMessageIndex = null;
            try {
              await ensurePartsTurn();
              await postTurnPart(
                account,
                sessionKey,
                partsMessageId,
                { part_id: crypto.randomUUID(), kind: "text", text: outText },
                payload.chat_id,
              );
              committedAnyText = true;
              postedAnyContent = true;
            } catch (err) {
              logError(
                api,
                `sellerclaw-ui: text part failed session_key=${sessionKey}: ${String(err)}`,
              );
            }
          }
        }
      },
      onRecordError: (err: unknown) => {
        logError(api, `sellerclaw-ui: inbound session record error: ${String(err)}`);
      },
      onDispatchError: (err: unknown, info: { kind: string }) => {
        logError(api, `sellerclaw-ui: inbound ${info.kind} reply error: ${String(err)}`);
      },
    });
    deferredTo = deferredToActiveRun(turnResult);
    if (deferredTo === "followup") {
      // Queued behind the run going in the session (a subagent report the message could not be
      // added to): its run starts once that one ends, and answers through the outbound road with
      // this message as its reply target. The owner's message stays pending until then — closed
      // now, it would read as answered while the reply was still to come, with no sign of the
      // agent at work and nothing for a stop to reach.
      logDelivery(api, `inbound turn queued_behind_active_run session_key=${sessionKey}`);
      await waitForQueuedRun(queuedSettled, ownerTurn.abort.signal);
    }
  };

  // Started eagerly, exactly as before: the HTTP route has already answered 202 and the owner is
  // watching the chat, so the turn must not wait on anything before it begins.
  const dispatchPromise = runDispatch(0);

  /**
   * How this turn ends, once the dispatch has settled.
   *
   * A dispatch that *threw* is a crash: ``failed``, exactly as before. A turn the owner stopped
   * closes quietly, whatever it left behind — a deliberate stop is neither an error nor a silence
   * to fill. Everything else hinges on two questions: did anything reach the owner, and did the
   * engine hand us a failure notice instead of an answer.
   *
   *  - an answer reached them → an ordinary turn (a tool-failure warning riding beside it is
   *    withheld);
   *  - nothing reached them and the run ended on its own — the silent token, a yield, a failed
   *    tool call with only the engine's warning to show for it → ask again, once, and let that
   *    answer land in this turn's message (``heldOpenForReask``). The owner wrote to the agent;
   *    a live turn has no ending where saying nothing is the
   *    answer (a chat of theirs showed only error notes under "report the status of the task",
   *    2026-09-21 — the agent had decided it "already sent the status");
   *  - a run that aborted (no error family attached), or whose connection broke → quiet close
   *    **and** resume, while attempts remain. The owner keeps whatever was already streamed or
   *    sent through the ``message`` tool, and the continuation delivers the finished work;
   *  - nothing reached them because the engine injected the message into a run already going
   *    (``steer`` queue mode) → closed quietly: the answer comes through that run's own turn. A
   *    message the engine queued behind that run instead is judged like any other, once its own
   *    run is over (see ``waitForQueuedRun``);
   *  - silent again after being asked → closed quietly: the agent chose it knowing nothing had
   *    arrived, and an error note would report a failure that never happened;
   *  - anything else — a failure family that needs a human (billing, auth, rate limit), attempts
   *    spent, or an unrecognised failure after the owner already had something (or after the
   *    re-ask) → ``failed``, so the owner is told rather than left with silence.
   *
   * A ``completed`` end with no parts is the cloud's "release the paired user message" marker
   * and leaves no bubble behind; ``failed`` keeps any partial text and attaches the retryable
   * note. Both branches already exist server-side.
   */
  const resolveTurnEnd = (): { status: "completed" | "failed"; branch: string } => {
    // Consumed unconditionally: a stop belongs to at most this turn. A run that raced the
    // abort to a normal finish must still clear the mark, or it would silence the next turn's
    // genuine failure.
    const ownerStopMarked = consumeOwnerAbort(sessionKey);
    if (ownerStopMarked || ownerTurn.abort.signal.aborted) {
      resetContinuations(sessionKey);
      return { status: "completed", branch: "owner_stop" };
    }
    // An answer sent with the ``message`` tool never passes through this dispatch, so what the
    // turn delivered itself is only half the question. Counted for this owner message: the
    // chat's other turns — and a subagent's report — send on the same road.
    const ownerAnswered =
      postedAnyContent ||
      (questionId
        ? replyDeliveredSince(questionId, turnStartedAt)
        : outboundDeliveredSince(payload.chat_id, turnStartedAt));
    /**
     * Ask the agent again for the reply the owner did not get — once per owner message.
     *
     * What a second silence means depends on how the run ended. One that finished on its own,
     * told outright that nothing reached the owner, chose silence again — the owner may well have
     * asked for it ("don't reply to this") — and an error note would report a failure that never
     * happened, so the turn closes quietly. One that cannot be vouched for (an unrecognised engine
     * failure with no verdict) surfaces as ``failed``: that is the owner's only sign it broke.
     */
    const askAgain = (
      branchWhenSpent: string,
      silenceChosen: boolean,
    ): { status: "completed" | "failed"; branch: string } => {
      if (params.unansweredReasked) {
        return silenceChosen
          ? { status: "completed", branch: "silent_after_reask" }
          : { status: "failed", branch: branchWhenSpent };
      }
      pendingUnansweredReask = true;
      return { status: "completed", branch: "unanswered_asking_again" };
    };
    if (!sawErrorFinal) {
      if (!ownerAnswered) {
        // Added to the run already going: that run's turn delivers the answer.
        if (deferredTo === "steer") {
          return { status: "completed", branch: "deferred_to_active_run mode=steer" };
        }
        return askAgain("failed_unanswered", true);
      }
      resetContinuations(sessionKey);
      return { status: "completed", branch: "normal" };
    }
    /**
     * Recover from a failure that is worth re-asking, while attempts remain.
     *
     * ``branchWhenRefused`` names why we did not recover, so the delivery timeline still says
     * which rule fired. ``runEndedOnItsOwn`` marks the verdicts where the notice may be no more
     * than a tool-failure warning beside a silent answer: that silence is asked again, like any
     * other, unless the notice names a failure only a person can fix. ``silenceChosen`` says the
     * run is known to have finished normally, so a repeat silence was the agent's choice.
     */
    const recoverIfTransport = (
      branchWhenRefused: string,
      runEndedOnItsOwn: boolean,
      silenceChosen = false,
    ): { status: "completed" | "failed"; branch: string } => {
      if (!isTransportTurnFailure(lastErrorFinalText)) {
        if (runEndedOnItsOwn && !ownerAnswered && !isHumanNeededFailure(lastErrorFinalText)) {
          return askAgain(branchWhenRefused, silenceChosen);
        }
        return { status: "failed", branch: branchWhenRefused };
      }
      const attempt = claimContinuation(sessionKey);
      if (attempt === null) return { status: "failed", branch: "failed_recovery_exhausted" };
      pendingContinuationAttempt = attempt;
      return {
        status: "completed",
        branch: `recovering_transport attempt=${attempt}/${MAX_CONTINUATIONS}`,
      };
    };

    const outcome = readRunOutcome(sessionKey);
    // No verdict at all. On the deployed runtime this is every failed turn, not an edge case:
    // the agent run loads its own plugin set (memory only), so our ``agent_end`` subscription —
    // registered in the gateway process — is never called. Falling straight through to "failed"
    // is what left a dropped connection as a dead end in the chat, so the engine's failure text
    // decides instead.
    if (!outcome) return recoverIfTransport("failed_no_outcome", true);
    if (outcome.success) {
      // The run itself finished fine; the error payload was a tool-failure warning the engine
      // appends BESIDE a real answer (``run/payloads.ts`` pushes those with ``isError: true``
      // too). With an answer in front of the owner — streamed here, or sent with the ``message``
      // tool — the turn is a success: the suppressed warning costs them an engine-worded
      // "⚠️ tool failed" line, which the agent's own text covers. With nothing in front of them,
      // the warning was all the run left: a silent answer is asked again, and a failure only a
      // person can fix (a mid-turn rate limit that ate the reply) is surfaced.
      if (ownerAnswered) {
        resetContinuations(sessionKey);
        return { status: "completed", branch: "completed_warning_suppressed" };
      }
      return recoverIfTransport("failed_error_family", true, true);
    }
    // An error family the runtime did attribute. Most of these need a human (billing, auth,
    // quota), but a provider connection that died mid-answer lands here too and is worth one
    // more try.
    if (outcome.hasError) return recoverIfTransport("failed_error_family", false);
    const attempt = claimContinuation(sessionKey);
    if (attempt === null) return { status: "failed", branch: "failed_recovery_exhausted" };
    pendingContinuationAttempt = attempt;
    return { status: "completed", branch: `recovering attempt=${attempt}/${MAX_CONTINUATIONS}` };
  };

  const finishTurn = async (forced?: "failed"): Promise<void> => {
    // Always finalize via ``turn/end``. If the dispatch produced no parts, open an
    // (empty) turn first so the paired user turn is completed rather than left
    // PROCESSING.
    // A rejected dispatch is a crash — except when the owner's stop caused the unwind: some
    // abort paths reject rather than resolve, and a deliberate stop must not wear an error
    // note. Consuming the mark here also keeps it from leaking into the next turn.
    const { status, branch } = forced
      ? consumeOwnerAbort(sessionKey) || ownerTurn.abort.signal.aborted
        ? { status: "completed" as const, branch: "owner_stop" }
        : { status: "failed" as const, branch: "dispatch_error" }
      : resolveTurnEnd();
    heldOpenForReask = !forced && pendingUnansweredReask && !partsTurnStarted;
    if (!heldOpenForReask) await ensurePartsTurn();
    // Closing line of the delivery timeline: how many pieces the turn produced, how it ended and
    // how long it ran. One delivery on a multi-minute turn means the owner watched an empty chat;
    // ``branch`` names which end-of-turn rule fired, and is the canary for engine drift — a
    // budget abort that stops reaching ``recovering``/``failed_*`` means ``isError`` no longer
    // arrives (see ``inbound-reply-with-reasoning.ts``).
    logDelivery(
      api,
      `inbound turn ${heldOpenForReask ? "held_open" : status} branch=${branch} ` +
        `deliveries=${deliveryCount} suppressed_chars=${suppressedErrorChars} ` +
        `${params.continuationAttempt ? `continuation=${params.continuationAttempt} ` : ""}` +
        `${params.unansweredReasked ? "reasked=true " : ""}` +
        `duration=${Math.round((Date.now() - turnStartedAt) / 1000)}s session_key=${sessionKey}`,
    );
    if (heldOpenForReask) return;
    try {
      await postTurnEnd(account, sessionKey, partsMessageId, payload.chat_id, status);
    } catch (err) {
      logError(api, `sellerclaw-ui: turn-end failed session_key=${sessionKey}: ${String(err)}`);
    }
  };

  /**
   * Follow the turn up the way ``finishTurn`` decided: resume a turn the run budget cut short, or
   * ask again for the reply the owner never got. Both re-enter this same path with a prompt of
   * ours, strictly after this turn is finalized.
   *
   * A re-ask of a turn held open takes over what that turn still holds — its assistant message,
   * its thought count, its in-flight slot — so the owner's message stays guarded and pending until
   * the reply lands.
   *
   * A failure to even start the follow-up is the one path that ends quiet with nobody coming
   * back, so it is logged as an error rather than swallowed.
   */
  const startPendingContinuation = async (): Promise<void> => {
    const attempt = pendingContinuationAttempt;
    const reask = pendingUnansweredReask;
    if (attempt === null && !reask) return;
    pendingContinuationAttempt = null;
    pendingUnansweredReask = false;
    if (ownerTurn.abort.signal.aborted) return;
    const carryOn = reask && heldOpenForReask;
    const label = reask
      ? "unanswered re-ask"
      : `continuation attempt=${attempt}/${MAX_CONTINUATIONS}`;
    try {
      await startInboundTurn({
        api,
        account,
        runtime,
        payload: {
          chat_id: payload.chat_id,
          agent_id: payload.agent_id,
          user_id: payload.user_id,
          text: asLiveChatPrompt(reask ? UNANSWERED_PROMPT : CONTINUATION_PROMPT, payload.effort),
          // Attachments were materialized by the interrupted turn and are already in its
          // session; re-sending them would upload them a second time.
          effort: payload.effort,
        },
        sessionKey,
        inboundMessageId: carryOn ? inboundMessageId : "",
        continuationAttempt: attempt ?? undefined,
        unansweredReasked: params.unansweredReasked || reask,
        partsMessageId: carryOn ? partsMessageId : undefined,
        thoughtSeqStart: carryOn ? thoughtSeq : undefined,
        questionId,
      });
      logDelivery(api, `inbound ${label} started session_key=${sessionKey}`);
    } catch (err) {
      if (carryOn && inboundMessageId) inFlightInboundMessageIds.delete(inboundMessageId);
      logError(
        api,
        `sellerclaw-ui: ${label} dispatch failed session_key=${sessionKey}: ${String(err)}`,
      );
    }
  };

  /**
   * Re-dispatch a turn the runtime refused before it admitted it.
   *
   * Bounded and conditional, because a retry is only ever right for a race:
   *
   *  * the rejection must name one (see {@link isAdmissionRaceFailure}) — anything else would
   *    fail identically and belongs in front of the owner;
   *  * nothing may have reached the chat yet. A dispatch that already streamed part of an answer
   *    was admitted; whatever killed it afterwards is a different failure, and re-running it
   *    would say the same things twice.
   *
   * The wait lets whatever held the session (a settle wake, a finishing subagent) land its own
   * state change first; without it the retry races the same claim again.
   */
  const dispatchWithAdmissionRetries = async (): Promise<void> => {
    let lastError: unknown;
    try {
      await dispatchPromise;
      return;
    } catch (err: unknown) {
      lastError = err;
    }
    for (let attempt = 1; attempt <= MAX_ADMISSION_REDISPATCHES; attempt += 1) {
      if (!isAdmissionRaceFailure(String(lastError)) || deliveryCount > 0 || partsTurnStarted) {
        break;
      }
      logDelivery(
        api,
        `inbound dispatch refused by an admission race, retrying ` +
          `attempt=${attempt}/${MAX_ADMISSION_REDISPATCHES} session_key=${sessionKey}`,
      );
      await new Promise((resolve) => setTimeout(resolve, ADMISSION_REDISPATCH_DELAY_MS * attempt));
      try {
        await runDispatch(attempt);
        return;
      } catch (err: unknown) {
        lastError = err;
      }
    }
    throw lastError;
  };

  try {
    try {
      await dispatchWithAdmissionRetries();
      await finishTurn();
    } catch (err: unknown) {
      logError(api, `sellerclaw-ui: inbound dispatch failed: ${String(err)}`);
      await finishTurn("failed");
    }
  } finally {
    // Free the in-flight slot only after the turn is finalized (turn/end posted or
    // failed). Until then a catch-up re-delivery of this same message is dropped; once
    // freed, a later re-delivery (e.g. the cloud was down when turn/end fired) re-runs.
    // A turn held open for its re-ask hands the slot on instead: the message is still being
    // answered.
    if (inboundMessageId && !heldOpenForReask) inFlightInboundMessageIds.delete(inboundMessageId);
    try {
      await startPendingContinuation();
    } finally {
      // Only now: a stop that lands while the follow-up is being set up still finds this turn.
      closeOwnerTurn(ownerTurn);
    }
  }
}

/** Until a queued message's run is over — or the owner stopped it, or the backstop ran out. */
async function waitForQueuedRun(settled: Promise<void>, stop: AbortSignal): Promise<void> {
  if (stop.aborted) return;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let onStop: (() => void) | undefined;
  try {
    await Promise.race([
      settled,
      new Promise<void>((resolve) => {
        onStop = () => resolve();
        stop.addEventListener("abort", onStop, { once: true });
      }),
      new Promise<void>((resolve) => {
        timer = setTimeout(resolve, QUEUED_TURN_MAX_WAIT_MS);
      }),
    ]);
  } finally {
    if (timer) clearTimeout(timer);
    if (onStop) stop.removeEventListener("abort", onStop);
  }
}

export function registerInboundRoute(api: OpenClawPluginApi): void {
  api.registerHttpRoute({
    // `/api/channels` prefix + `auth: "gateway"`: OpenClaw authenticates the request
    // against the gateway token BEFORE the handler and grants the agent run its operator
    // scopes — `operator.write` is required for `sessions_spawn`, `operator.read` for reading
    // other sessions (see SUPERVISOR_TURN_SCOPE_SURFACE for why the default surface is not enough).
    // A plugin-authed route would run the whole turn with an empty operator scope.
    path: "/api/channels/sellerclaw-ui/inbound",
    auth: "gateway",
    gatewayRuntimeScopeSurface: SUPERVISOR_TURN_SCOPE_SURFACE,
    handler: async (req, res) => {
      // Must run before the turn is dispatched: OpenClaw's route scope has no gateway resolver,
      // and without it a turn that spawns 2+ subagents can never wake this chat again.
      ensureRouteScopeGatewayResolver();
      let account: ScwUiAccount;
      try {
        account = resolveSellerclawUiAccount(api.config);
      } catch {
        res.statusCode = 503;
        res.end(JSON.stringify({ error: "Channel not configured" }));
        return true;
      }

      const readResult = await readJsonWebhookBodyOrReject({ req, res });
      if (
        !readResult ||
        typeof readResult !== "object" ||
        !("ok" in readResult) ||
        !(readResult as { ok: boolean }).ok
      ) {
        return true;
      }
      const body = (readResult as { ok: true; value: unknown }).value;
      if (!body || typeof body !== "object") {
        res.statusCode = 400;
        res.end(JSON.stringify({ error: "Invalid JSON body" }));
        return true;
      }

      const payload = body as unknown as InboundPayload;
      if (!payload.chat_id || !payload.text?.trim()) {
        res.statusCode = 400;
        res.end(JSON.stringify({ error: "chat_id and text required" }));
        return true;
      }

      let runtime: ReturnType<typeof getRuntime>;
      try {
        runtime = getRuntime();
      } catch (err) {
        logError(api, `sellerclaw-ui: getRuntime failed: ${String(err)}`);
        res.statusCode = 500;
        res.end(JSON.stringify({ error: "Plugin runtime not available" }));
        return true;
      }

      const sessionKey = `agent:${payload.agent_id}:sellerclaw-ui:direct:${payload.chat_id}`;

      // Drop a duplicate dispatch of the SAME cloud message while its turn is still in
      // flight (a catch-up re-delivery racing the original run). Once the turn finishes the
      // id is freed, so a later re-delivery of a message whose result was lost re-processes.
      const inboundMessageId = payload.message_id?.trim() ?? "";
      if (inboundMessageId && inFlightInboundMessageIds.has(inboundMessageId)) {
        logInfo(
          api,
          `sellerclaw-ui: inbound dropped (turn already in flight) message_id=${inboundMessageId} chat_id=${payload.chat_id}`,
        );
        res.statusCode = 202;
        res.end(JSON.stringify({ ok: true, deduped: true }));
        return true;
      }
      if (inboundMessageId) inFlightInboundMessageIds.add(inboundMessageId);

      logInfo(
        api,
        `sellerclaw-ui: inbound accepted chat_id=${payload.chat_id} agent_id=${payload.agent_id} expected_session_key=${sessionKey}${
          payload.redelivery ? " redelivery=true" : ""
        }`,
      );

      res.statusCode = 202;
      res.end(JSON.stringify({ ok: true }));

      // The turn runs to completion INSIDE this handler, after the 202 is already flushed.
      // Since OpenClaw 2026.8 a plugin route runs under a Gateway root-work admission that is
      // released the moment the handler returns; work that outlives it is refused with
      // `GatewayDrainingError: Gateway is draining` — the whole chat went silent that way.
      // Staying inside the handler also keeps the request's operator scope, which the run needs.
      await startInboundTurn({ api, account, runtime, payload, sessionKey, inboundMessageId });

      return true;
    },
  });
}

interface ScheduledRunPayload {
  run_id: string;
  agent_id: string;
  user_id: string;
  instruction: string;
  /** Isolated session key chosen by the cloud; informational — isolation here is by run_id. */
  session_key?: string;
}

/**
 * Scheduled-task run route: the cloud hands one recurring-task occurrence here — NOT a chat.
 *
 * Unlike the inbound chat route this never streams turn parts and never persists a chat message.
 * It runs the instruction in an isolated per-run session, accumulates the agent's final reply as a
 * summary, and — deterministically when the run finishes (success OR failure) — POSTs the structured
 * outcome to the cloud's ``/agent/scheduled-tasks/run`` webhook, echoing ``run_id`` so the cloud
 * folds it into the run journal idempotently. Gateway-authenticated with the same scope surface as
 * the inbound route, so the run's tools get operator scopes rather than an empty set.
 */
export function registerScheduledRunRoute(api: OpenClawPluginApi): void {
  const SUMMARY_MAX = 60_000;
  api.registerHttpRoute({
    path: "/api/channels/sellerclaw-ui/scheduled-run",
    auth: "gateway",
    gatewayRuntimeScopeSurface: SUPERVISOR_TURN_SCOPE_SURFACE,
    handler: async (req, res) => {
      // Must run before the turn is dispatched: OpenClaw's route scope has no gateway resolver,
      // and without it a turn that spawns 2+ subagents can never wake this chat again.
      ensureRouteScopeGatewayResolver();
      let account: ScwUiAccount;
      try {
        account = resolveSellerclawUiAccount(api.config);
      } catch {
        res.statusCode = 503;
        res.end(JSON.stringify({ error: "Channel not configured" }));
        return true;
      }

      const readResult = await readJsonWebhookBodyOrReject({ req, res });
      if (
        !readResult ||
        typeof readResult !== "object" ||
        !("ok" in readResult) ||
        !(readResult as { ok: boolean }).ok
      ) {
        return true;
      }
      const body = (readResult as { ok: true; value: unknown }).value;
      if (!body || typeof body !== "object") {
        res.statusCode = 400;
        res.end(JSON.stringify({ error: "Invalid JSON body" }));
        return true;
      }

      const payload = body as unknown as ScheduledRunPayload;
      const runId = payload.run_id?.trim() ?? "";
      const instruction = payload.instruction?.trim() ?? "";
      if (!runId || !instruction) {
        res.statusCode = 400;
        res.end(JSON.stringify({ error: "run_id and instruction required" }));
        return true;
      }

      let runtime: ReturnType<typeof getRuntime>;
      try {
        runtime = getRuntime();
      } catch (err) {
        logError(api, `sellerclaw-ui: getRuntime failed: ${String(err)}`);
        res.statusCode = 500;
        res.end(JSON.stringify({ error: "Plugin runtime not available" }));
        return true;
      }

      logInfo(api, `sellerclaw-ui: scheduled-run accepted run_id=${runId} user_id=${payload.user_id}`);
      res.statusCode = 202;
      res.end(JSON.stringify({ ok: true }));

      // Isolated per-run conversation so a scheduled run never touches a user chat session.
      const conversationId = `scheduled-task:${runId}`;

      // Accumulate the agent's final reply as the run summary. The engine streams each block and
      // then re-delivers the consolidated ``final`` — prefer the final per assistant-message index.
      const textByIndex = new Map<number, string>();
      let deliveries = 0;
      const deliver = async (
        replyPayload: unknown,
        dispatchInfo?: { kind?: string; assistantMessageIndex?: number },
      ): Promise<void> => {
        if (
          typeof replyPayload === "object" &&
          replyPayload !== null &&
          isReasoningReplyPayload(replyPayload as Record<string, unknown>)
        ) {
          return; // reasoning ("thinking") is not part of the outcome summary
        }
        deliveries++;
        const { text } = readDeliverPayload(replyPayload);
        const clean = text.trim();
        if (!clean) return;
        const idx = dispatchInfo?.assistantMessageIndex ?? 0;
        if (dispatchInfo?.kind === "final" || !textByIndex.has(idx)) {
          textByIndex.set(idx, clean);
        } else {
          textByIndex.set(idx, `${textByIndex.get(idx) ?? ""}\n\n${clean}`);
        }
      };

      const summarize = (): string =>
        [...textByIndex.keys()]
          .sort((a, b) => a - b)
          .map((k) => textByIndex.get(k) ?? "")
          .filter(Boolean)
          .join("\n\n")
          .slice(0, SUMMARY_MAX);

      const report = async (status: "ok" | "error", error?: string): Promise<void> => {
        try {
          const summary = summarize();
          if (!summary) {
            // A run the owner cannot read is a defect, not a quiet success — make it greppable.
            logInfo(
              api,
              `sellerclaw-ui: scheduled-run produced no summary run_id=${runId} status=${status} deliveries=${deliveries}`,
            );
          }
          await postScheduledTaskRun(account, {
            runId,
            status,
            ...(summary ? { summary } : {}),
            ...(error ? { error: error.slice(0, SUMMARY_MAX) } : {}),
          });
        } catch (err) {
          logError(
            api,
            `sellerclaw-ui: scheduled-run report failed run_id=${runId}: ${String(err)}`,
          );
        }
      };

      // Awaited inside the handler for the same reason as the inbound route: work that outlives
      // it loses the Gateway root-work admission and is refused as "gateway is draining".
      await dispatchInboundDirectDmWithReasoning({
        cfg: api.config,
        runtime,
        channel: "sellerclaw-ui",
        channelLabel: "SellerClaw UI",
        accountId: "default",
        peer: { kind: "direct", id: conversationId },
        senderId: payload.user_id,
        senderAddress: `sellerclaw-ui:${payload.user_id}`,
        recipientAddress: `sellerclaw-ui:direct:${conversationId}`,
        conversationLabel: conversationId,
        rawBody: instruction,
        messageId: crypto.randomUUID(),
        timestamp: Date.now(),
        commandAuthorized: true,
        deliver,
        onRecordError: (err: unknown) =>
          logError(api, `sellerclaw-ui: scheduled-run session record error: ${String(err)}`),
        onDispatchError: (err: unknown, info: { kind: string }) =>
          logError(api, `sellerclaw-ui: scheduled-run ${info.kind} reply error: ${String(err)}`),
      })
        .then(() => report("ok"))
        .catch(async (err: unknown) => {
          logError(
            api,
            `sellerclaw-ui: scheduled-run dispatch failed run_id=${runId}: ${String(err)}`,
          );
          await report("error", String(err));
        });

      return true;
    },
  });
}

interface FeasibilityCheckPayload {
  task_id: string;
  agent_id: string;
  user_id: string;
  instruction: string;
  session_key?: string;
}

/**
 * Parse the machine-readable ``VERDICT:`` line the feasibility assessment ends with.
 *
 * Returns ``null`` when no verdict line is present, so a parse-miss leaves the task's static
 * feasibility floor untouched (never downgrade a working task on a formatting slip). The rest of
 * the reply becomes the reusable ``approach``. Exported for unit testing.
 */
export function parseFeasibilityVerdict(
  text: string,
): { feasible: boolean; missing: string[]; approach: string } | null {
  const m = text.match(/VERDICT:\s*feasible\s*=\s*(yes|no)\b[^\n]*?missing\s*=\s*([^\n]*)/i);
  if (!m) return null;
  const feasible = (m[1] ?? "").toLowerCase() === "yes";
  const missingRaw = (m[2] ?? "").trim();
  const missing =
    !missingRaw || missingRaw.toLowerCase() === "none"
      ? []
      : missingRaw
          .split(",")
          .map((s) => s.trim())
          .filter(Boolean);
  const approach = text.replace(/^\s*VERDICT:.*$/im, "").trim();
  return { feasible, missing, approach };
}

/**
 * Feasibility-check route: the cloud asks whether the agent can fully do a task (NOT a chat).
 *
 * Runs the assessment in an isolated per-task session, accumulates the agent's reply, parses the
 * trailing ``VERDICT:`` line, and POSTs the structured verdict to
 * ``/agent/scheduled-tasks/feasibility`` (echoing ``task_id``). A reply without a parseable verdict
 * is left alone — the cloud keeps the static floor. Gateway-authenticated like the inbound route.
 */
export function registerFeasibilityCheckRoute(api: OpenClawPluginApi): void {
  const APPROACH_MAX = 32_000;
  api.registerHttpRoute({
    path: "/api/channels/sellerclaw-ui/feasibility-check",
    auth: "gateway",
    handler: async (req, res) => {
      // Must run before the turn is dispatched: OpenClaw's route scope has no gateway resolver,
      // and without it a turn that spawns 2+ subagents can never wake this chat again.
      ensureRouteScopeGatewayResolver();
      let account: ScwUiAccount;
      try {
        account = resolveSellerclawUiAccount(api.config);
      } catch {
        res.statusCode = 503;
        res.end(JSON.stringify({ error: "Channel not configured" }));
        return true;
      }

      const readResult = await readJsonWebhookBodyOrReject({ req, res });
      if (
        !readResult ||
        typeof readResult !== "object" ||
        !("ok" in readResult) ||
        !(readResult as { ok: boolean }).ok
      ) {
        return true;
      }
      const body = (readResult as { ok: true; value: unknown }).value;
      if (!body || typeof body !== "object") {
        res.statusCode = 400;
        res.end(JSON.stringify({ error: "Invalid JSON body" }));
        return true;
      }

      const payload = body as unknown as FeasibilityCheckPayload;
      const taskId = payload.task_id?.trim() ?? "";
      const instruction = payload.instruction?.trim() ?? "";
      if (!taskId || !instruction) {
        res.statusCode = 400;
        res.end(JSON.stringify({ error: "task_id and instruction required" }));
        return true;
      }

      let runtime: ReturnType<typeof getRuntime>;
      try {
        runtime = getRuntime();
      } catch (err) {
        logError(api, `sellerclaw-ui: getRuntime failed: ${String(err)}`);
        res.statusCode = 500;
        res.end(JSON.stringify({ error: "Plugin runtime not available" }));
        return true;
      }

      logInfo(api, `sellerclaw-ui: feasibility-check accepted task_id=${taskId}`);
      res.statusCode = 202;
      res.end(JSON.stringify({ ok: true }));

      const conversationId = `feasibility-check:${taskId}`;

      const textByIndex = new Map<number, string>();
      const deliver = async (
        replyPayload: unknown,
        dispatchInfo?: { kind?: string; assistantMessageIndex?: number },
      ): Promise<void> => {
        if (
          typeof replyPayload === "object" &&
          replyPayload !== null &&
          isReasoningReplyPayload(replyPayload as Record<string, unknown>)
        ) {
          return;
        }
        const { text } = readDeliverPayload(replyPayload);
        const clean = text.trim();
        if (!clean) return;
        const idx = dispatchInfo?.assistantMessageIndex ?? 0;
        if (dispatchInfo?.kind === "final" || !textByIndex.has(idx)) {
          textByIndex.set(idx, clean);
        } else {
          textByIndex.set(idx, `${textByIndex.get(idx) ?? ""}\n\n${clean}`);
        }
      };

      const fullReply = (): string =>
        [...textByIndex.keys()]
          .sort((a, b) => a - b)
          .map((k) => textByIndex.get(k) ?? "")
          .filter(Boolean)
          .join("\n\n");

      // Awaited inside the handler for the same reason as the inbound route: work that outlives
      // it loses the Gateway root-work admission and is refused as "gateway is draining".
      await dispatchInboundDirectDmWithReasoning({
        cfg: api.config,
        runtime,
        channel: "sellerclaw-ui",
        channelLabel: "SellerClaw UI",
        accountId: "default",
        peer: { kind: "direct", id: conversationId },
        senderId: payload.user_id,
        senderAddress: `sellerclaw-ui:${payload.user_id}`,
        recipientAddress: `sellerclaw-ui:direct:${conversationId}`,
        conversationLabel: conversationId,
        rawBody: instruction,
        messageId: crypto.randomUUID(),
        timestamp: Date.now(),
        commandAuthorized: true,
        deliver,
        onRecordError: (err: unknown) =>
          logError(api, `sellerclaw-ui: feasibility-check session record error: ${String(err)}`),
        onDispatchError: (err: unknown, info: { kind: string }) =>
          logError(api, `sellerclaw-ui: feasibility-check ${info.kind} reply error: ${String(err)}`),
      })
        .then(async () => {
          const verdict = parseFeasibilityVerdict(fullReply());
          if (!verdict) {
            logInfo(
              api,
              `sellerclaw-ui: feasibility-check no verdict parsed task_id=${taskId} (keeping static floor)`,
            );
            return;
          }
          try {
            await postScheduledTaskFeasibility(account, {
              taskId,
              feasible: verdict.feasible,
              missing: verdict.missing,
              ...(verdict.approach ? { approach: verdict.approach.slice(0, APPROACH_MAX) } : {}),
            });
          } catch (err) {
            logError(
              api,
              `sellerclaw-ui: feasibility-check report failed task_id=${taskId}: ${String(err)}`,
            );
          }
        })
        .catch((err: unknown) => {
          // Can't assess (dispatch failed) — leave the static feasibility floor in place.
          logError(
            api,
            `sellerclaw-ui: feasibility-check dispatch failed task_id=${taskId}: ${String(err)}`,
          );
        });

      return true;
    },
  });
}

interface AbortPayload {
  chat_id: string;
  agent_id: string;
}

/**
 * Stop route: the cloud forwards a user "stop" here when it wants the in-flight
 * OpenClaw reply aborted. Every turn open in the chat is aborted — a message still waiting in the
 * engine's queue or for its step in the running turn is dropped there — and then the active
 * embedded run for the chat's session key. Idempotent — nothing in flight is a benign no-op.
 */
export function registerAbortRoute(api: OpenClawPluginApi): void {
  api.registerHttpRoute({
    // Gateway-authenticated like the inbound route (see registerInboundRoute).
    path: "/api/channels/sellerclaw-ui/abort",
    auth: "gateway",
    handler: async (req, res) => {
      const readResult = await readJsonWebhookBodyOrReject({ req, res });
      if (
        !readResult ||
        typeof readResult !== "object" ||
        !("ok" in readResult) ||
        !(readResult as { ok: boolean }).ok
      ) {
        return true;
      }
      const body = (readResult as { ok: true; value: unknown }).value;
      if (!body || typeof body !== "object") {
        res.statusCode = 400;
        res.end(JSON.stringify({ error: "Invalid JSON body" }));
        return true;
      }

      const payload = body as unknown as AbortPayload;
      if (!payload.chat_id || !payload.agent_id) {
        res.statusCode = 400;
        res.end(JSON.stringify({ error: "chat_id and agent_id required" }));
        return true;
      }

      const sessionKey = `agent:${payload.agent_id}:sellerclaw-ui:direct:${payload.chat_id}`;
      // The chat's open turns first: a message waiting in the engine's queue runs the moment the
      // active run ends, so it has to be cancelled before that run is aborted.
      const stoppedTurns = abortOwnerTurns(payload.chat_id);
      const sessionId = resolveActiveEmbeddedRunSessionId(sessionKey);
      if (sessionId) {
        // Record the stop before aborting: the run unwinds into the same terminal state as a
        // run-budget death and delivers the same engine failure notice, and ``agent_end`` cannot
        // tell the two apart. We can, because we are the ones causing this one — and a stop must
        // neither show an error nor resume itself. Marked only when a run was actually aborted,
        // so a no-op stop leaves nothing behind to silence a later, genuine failure.
        markOwnerAbort(sessionKey);
        abortAgentHarnessRun(sessionId);
        logInfo(
          api,
          `sellerclaw-ui: abort run session_key=${sessionKey} session_id=${sessionId} ` +
            `turns=${stoppedTurns}`,
        );
      } else {
        logInfo(
          api,
          `sellerclaw-ui: abort (no active run) session_key=${sessionKey} turns=${stoppedTurns}`,
        );
      }

      res.statusCode = 202;
      res.end(JSON.stringify({ ok: true, aborted: Boolean(sessionId) || stoppedTurns > 0 }));
      return true;
    },
  });
}
