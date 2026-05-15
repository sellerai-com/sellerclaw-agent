import type { OpenClawPluginApi } from "openclaw/plugin-sdk/core";
import { dispatchInboundDirectDmWithRuntime } from "openclaw/plugin-sdk/channel-inbound";
import { readJsonWebhookBodyOrReject } from "openclaw/plugin-sdk/webhook-ingress";
import { saveMediaBuffer } from "openclaw/plugin-sdk/media-store";

import { resolveSellerclawUiAccount } from "./channel.js";
import {
  postOpenclawWebhook,
  postWebhookMediaMessage,
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
}

/**
 * Plugin logger wrappers: when OpenClaw runs without a logger (or with `info`/`error`
 * stripped — happens with `redactSensitive: "tools"`), optional chaining swallows
 * media-delivery failures silently. Fall back to `console.*` so the operator can still
 * see what happened when chasing a "image not delivered" bug.
 */
function logInfo(api: OpenClawPluginApi, msg: string): void {
  if (api.logger?.info) {
    api.logger.info(msg);
    return;
  }
  console.info(msg);
}

function logError(api: OpenClawPluginApi, msg: string): void {
  if (api.logger?.error) {
    api.logger.error(msg);
    return;
  }
  console.error(msg);
}

function logWarn(api: OpenClawPluginApi, msg: string): void {
  if (api.logger?.warn) {
    api.logger.warn(msg);
    return;
  }
  console.warn(msg);
}

interface ImagePart {
  url: string;
  filename: string;
  contentType: string;
}

interface FilePart {
  url: string;
  filename: string;
  contentType: string;
}

function asStringField(value: unknown): string {
  return typeof value === "string" && value.trim() ? value.trim() : "";
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
    if (p.type === "image_url" && p.image_url && typeof p.image_url === "object") {
      const url = asStringField((p.image_url as { url?: unknown }).url);
      if (url) images.push({ url, filename, contentType });
      continue;
    }
    if (p.type === "file_url" && p.file_url && typeof p.file_url === "object") {
      const url = asStringField((p.file_url as { url?: unknown }).url);
      if (url) files.push({ url, filename, contentType });
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
 * For non-image files we emit the canonical ``MEDIA:`` marker (filename on
 * one line, MEDIA tag with the absolute path on the next). OpenClaw's
 * media-understanding / file-extraction pipeline detects this and loads the
 * file: PDF/DOCX text is extracted natively, other types are exposed to the
 * agent under ``media/inbound/`` via ``Read``/``Bash`` tools.
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

const STREAM_DELTA_PATH = "/internal/openclaw/stream-delta";
const STREAM_END_PATH = "/internal/openclaw/stream-end";

/**
 * Block-starting markdown patterns whose presence at the start of a new
 * delta means the previous delta MUST be terminated by a paragraph break
 * (`\n\n`), otherwise the structure collapses:
 *   - ATX headings (`# `, `## `, ...)
 *   - fenced code blocks (` ``` `)
 *   - blockquotes (`> `)
 *   - bullet/ordered lists (`- `, `* `, `+ `, `1. `, `1) `)
 *   - thematic breaks (`---`, `***`, `___`)
 *   - GFM table rows (`|`)
 */
const MARKDOWN_BLOCK_START_RE =
  /^(?:#{1,6}\s|```|>(?:\s|$)|---|\*\*\*|___|\||[-*+]\s|\d+[.)]\s)/;

/** ATX heading at line start (`# `, `## `, ..., up to `###### `). */
const ATX_HEADING_RE = /^#{1,6}\s/;

/**
 * Picks a joiner string to insert between two consecutive streaming deltas.
 *
 * The OpenClaw block-streaming chunker cuts the assistant reply at internal
 * boundaries and drops the whitespace it cut on. We can't see what was
 * dropped, so we use heuristics on the visible content at the boundary:
 *
 *   * Markdown block-starter at the head of the next delta → `\n\n`. Without
 *     this the heading / list / fence would be absorbed into the previous
 *     paragraph (or worse, a fence would never close).
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
 * starts with a list / quote / table marker. Those structures can span an
 * arbitrary amount of content, and the chunker much more often cuts inside
 * a long list item than right after one. Defaulting to a space there keeps
 * mid-item continuations intact at the price of an occasional missed visual
 * paragraph break.
 *
 * Exported for unit-testing without spinning up the HTTP route.
 */
export function pickDeltaJoin(prevTail: string, nextHead: string): string {
  if (prevTail === "") return "";
  const tail = prevTail.replace(/\s+$/, "");
  const head = nextHead.replace(/^\s+/, "");
  if (tail === "" || head === "") return "";
  if (MARKDOWN_BLOCK_START_RE.test(head)) return "\n\n";
  if (/```$/.test(tail)) return "\n\n";
  const lastNl = tail.lastIndexOf("\n");
  const tailLastLine = lastNl === -1 ? tail : tail.slice(lastNl + 1);
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
 * Exported for unit-testing without spinning up the HTTP route.
 */
export function readDeliverPayload(raw: unknown): { text: string; mediaUrls: string[] } {
  if (!raw || typeof raw !== "object") return { text: "", mediaUrls: [] };
  const p = raw as Record<string, unknown>;
  const text = typeof p.text === "string" ? p.text : "";
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
  return { text, mediaUrls };
}

/** POST one streaming block to SellerClaw; logs failures only (does not throw). */
async function postStreamDeltaBestEffort(
  api: OpenClawPluginApi,
  account: ScwUiAccount,
  sessionKey: string,
  text: string,
) {
  const url = `${account.apiBaseUrl.replace(/\/$/, "")}${STREAM_DELTA_PATH}`;
  try {
    await postOpenclawWebhook(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${account.agentApiKey}`,
      },
      body: JSON.stringify({
        user_id: account.userId,
        session_key: sessionKey,
        text,
      }),
    });
  } catch (err) {
    logWarn(api, `sellerclaw-ui: stream-delta request failed url=${url}: ${String(err)}`);
  }
}

/** Notify backend that agent run finished. Best-effort: never throws. */
async function postStreamEndBestEffort(
  api: OpenClawPluginApi,
  account: ScwUiAccount,
  sessionKey: string,
) {
  const url = `${account.apiBaseUrl.replace(/\/$/, "")}${STREAM_END_PATH}`;
  try {
    await postOpenclawWebhook(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${account.agentApiKey}`,
      },
      body: JSON.stringify({
        user_id: account.userId,
        session_key: sessionKey,
      }),
    });
  } catch (err) {
    logWarn(api, `sellerclaw-ui: stream-end request failed url=${url}: ${String(err)}`);
  }
}

export function registerInboundRoute(api: OpenClawPluginApi): void {
  api.registerHttpRoute({
    path: "/channels/sellerclaw-ui/inbound",
    auth: "plugin",
    handler: async (req, res) => {
      const authHeader = req.headers.authorization;
      if (!authHeader?.startsWith("Bearer ")) {
        res.statusCode = 401;
        res.end(JSON.stringify({ error: "Missing auth" }));
        return true;
      }

      let account: ScwUiAccount;
      try {
        account = resolveSellerclawUiAccount(api.config);
      } catch {
        res.statusCode = 503;
        res.end(JSON.stringify({ error: "Channel not configured" }));
        return true;
      }

      const token = authHeader.slice(7);
      if (token !== account.internalWebhookSecret) {
        res.statusCode = 401;
        res.end(JSON.stringify({ error: "Invalid token" }));
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
      logInfo(
        api,
        `sellerclaw-ui: inbound accepted chat_id=${payload.chat_id} agent_id=${payload.agent_id} expected_session_key=${sessionKey}`,
      );

      res.statusCode = 202;
      res.end(JSON.stringify({ ok: true }));

      const dispatchPromise = (async () => {
        const attachmentMarkers = await materializeAttachmentsForAgent(
          api,
          account,
          payload.raw_content ?? [],
        );
        const rawBody = [payload.text.trim(), ...attachmentMarkers]
          .filter((part) => part && part.length > 0)
          .join("\n");

        // Tail of the previously delivered delta. `pickDeltaJoin` needs
        // enough suffix to recognise both a closing code fence and the full
        // last line (for the ATX-heading check). 512 chars comfortably covers
        // any realistic heading / fence while keeping per-session memory
        // bounded.
        let prevTail = "";
        const TAIL_KEEP_CHARS = 512;
        // Trimmed text-only delta payloads we've already streamed this turn —
        // OpenClaw's block dispatcher can re-deliver the same text on the
        // consolidated final payload, and we don't want to send it as a
        // stream-delta twice. This set gates the *text-only* path; the media
        // path always uses the deliver text as caption (see below) — the
        // buffered delta gets orphaned anyway when the media POST closes the
        // pending user turn before `stream-end` lands.
        const emittedTexts = new Set<string>();
        // Source paths/URLs already delivered as media this turn (same reason).
        const sentMedia = new Set<string>();

        await dispatchInboundDirectDmWithRuntime({
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
          messageId: payload.message_id ?? crypto.randomUUID(),
          timestamp: Date.now(),
          commandAuthorized: true,
          deliver: async (replyPayload: unknown) => {
            const { text, mediaUrls } = readDeliverPayload(replyPayload);

            // Media reply (e.g. an `image_generate` result surfaced via a
            // `MEDIA:` directive). Upload local container artifacts to cloud
            // File Storage and deliver each as its own chat message with
            // `raw_content`, mirroring the `sendImage` channel handler. The
            // stream-delta endpoint is text-only, so media cannot ride it.
            if (mediaUrls.length > 0) {
              const trimmed = text.trim();
              // Use the reply text as the caption of the FIRST media item this
              // turn. We don't dedup against ``emittedTexts`` here: the backend
              // ingest path (services.ingest_openclaw_ui_message) closes the
              // pending user turn synchronously, which makes the subsequent
              // ``stream-end`` a no-op and orphans any text we streamed as a
              // delta. Carrying the text as caption is the only place it
              // survives — otherwise the UI shows the cloud URL (postWebhook
              // text fallback) instead of the actual assistant prose.
              let caption = trimmed || "";
              for (const rawUrl of mediaUrls) {
                if (sentMedia.has(rawUrl)) continue;
                sentMedia.add(rawUrl);
                logInfo(
                  api,
                  `sellerclaw-ui: media delivery start session_key=${sessionKey} source=${rawUrl}`,
                );
                try {
                  const { url, contentType } = await resolveOutboundMediaUrl(account, rawUrl);
                  logInfo(
                    api,
                    `sellerclaw-ui: media uploaded session_key=${sessionKey} cloud_url=${url} content_type=${contentType || "?"}`,
                  );
                  await postWebhookMediaMessage(account, sessionKey, {
                    mediaUrl: url,
                    contentType,
                    caption,
                    chatId: payload.chat_id,
                    messageId: crypto.randomUUID(),
                  });
                  logInfo(
                    api,
                    `sellerclaw-ui: media delivered session_key=${sessionKey} captioned=${Boolean(
                      caption.trim(),
                    )} cloud_url=${url}`,
                  );
                  if (caption.trim()) emittedTexts.add(caption.trim());
                  caption = "";
                } catch (err) {
                  logError(
                    api,
                    `sellerclaw-ui: media delivery failed source=${rawUrl} session_key=${sessionKey}: ${String(err)}`,
                  );
                }
              }
              return;
            }

            // Text-only delta. Skip exact duplicates: the block dispatcher
            // re-delivers the same text on the consolidated final payload.
            const trimmed = text.trim();
            if (!trimmed || emittedTexts.has(trimmed)) return;
            emittedTexts.add(trimmed);
            const joiner = pickDeltaJoin(prevTail, text);
            const outText = joiner + text;
            prevTail = outText.slice(-TAIL_KEEP_CHARS);
            logInfo(
              api,
              `sellerclaw-ui: deliver block len=${outText.length} session_key=${sessionKey}`,
            );
            await postStreamDeltaBestEffort(api, account, sessionKey, outText);
          },
          onRecordError: (err: unknown) => {
            logError(api, `sellerclaw-ui: inbound session record error: ${String(err)}`);
          },
          onDispatchError: (err: unknown, info: { kind: string }) => {
            logError(api, `sellerclaw-ui: inbound ${info.kind} reply error: ${String(err)}`);
          },
        });
      })();

      void dispatchPromise
        .then(() => postStreamEndBestEffort(api, account, sessionKey))
        .catch((err: unknown) => {
          logError(api, `sellerclaw-ui: inbound dispatch failed: ${String(err)}`);
          void postStreamEndBestEffort(api, account, sessionKey);
        });

      return true;
    },
  });
}
