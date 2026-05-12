import type { OpenClawPluginApi } from "openclaw/plugin-sdk/core";
import { dispatchInboundDirectDmWithRuntime } from "openclaw/plugin-sdk/channel-inbound";
import { readJsonWebhookBodyOrReject } from "openclaw/plugin-sdk/webhook-ingress";
import { saveMediaBuffer } from "openclaw/plugin-sdk/media-store";

import { resolveSellerclawUiAccount } from "./channel.js";
import { postOpenclawWebhook, type ScwUiAccount } from "./send.js";
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

async function persistImageMarker(
  api: OpenClawPluginApi,
  account: ScwUiAccount,
  image: ImagePart,
): Promise<string> {
  const rewritten = rewriteUrlHost(image.url, account.apiBaseUrl);
  const displayName = image.filename || filenameFromUrl(rewritten, "image");
  try {
    const { buffer, contentType } = await downloadAttachment(rewritten, account.agentApiKey);
    const finalContentType = image.contentType || contentType;
    const saved = await saveMediaBuffer(buffer, finalContentType, "inbound", undefined, displayName);
    // Reference the saved file via its absolute path. The runtime's image
    // detection (``MESSAGE_IMAGE_PATTERN``) picks up ``[Image: source: <abs>]``
    // and loads it as a vision block in the user prompt. We deliberately do
    // not use ``media://inbound/<id>``: that claim-check URI only resolves
    // when the Gateway pre-registers the id in ``attachmentUris``, which
    // channel plugins don't have access to.
    return `[Image: source: ${saved.path}]`;
  } catch (err) {
    api.logger.warn?.(
      `sellerclaw-ui: image attachment fetch/save failed for ${displayName}: ${String(err)}`,
    );
    return `[attachment unavailable: ${displayName}]`;
  }
}

function formatFileLink(account: ScwUiAccount, file: FilePart): string {
  const rewritten = rewriteUrlHost(file.url, account.apiBaseUrl);
  const displayName = file.filename || filenameFromUrl(rewritten, "file");
  return `[${displayName}](${rewritten})`;
}

async function materializeAttachmentsForAgent(
  api: OpenClawPluginApi,
  account: ScwUiAccount,
  rawContent: unknown,
): Promise<string[]> {
  const { images, files } = extractAttachmentParts(rawContent);
  if (images.length === 0 && files.length === 0) return [];
  const imageMarkers = await Promise.all(
    images.map((img) => persistImageMarker(api, account, img)),
  );
  const fileLinks = files.map((f) => formatFileLink(account, f));
  return [...imageMarkers, ...fileLinks];
}

const STREAM_DELTA_PATH = "/internal/openclaw/stream-delta";
const STREAM_END_PATH = "/internal/openclaw/stream-end";

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
    api.logger.warn?.(`sellerclaw-ui: stream-delta request failed: ${String(err)}`);
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
    api.logger.warn?.(`sellerclaw-ui: stream-end request failed: ${String(err)}`);
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
        api.logger.error?.(`sellerclaw-ui: getRuntime failed: ${String(err)}`);
        res.statusCode = 500;
        res.end(JSON.stringify({ error: "Plugin runtime not available" }));
        return true;
      }

      const sessionKey = `agent:${payload.agent_id}:sellerclaw-ui:direct:${payload.chat_id}`;
      api.logger.info?.(
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

        // OpenClaw chunker can drop a whitespace at the cut point when it
        // falls through to whitespace/hard break (no joiner is defined for
        // those tiers). We restore the missing space when both sides of the
        // boundary are "word-like" — skip if either side is whitespace,
        // opening or closing punctuation.
        let lastChar = "";

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
            const text =
              replyPayload && typeof replyPayload === "object" && "text" in replyPayload
                ? String((replyPayload as Record<string, unknown>).text ?? "")
                : "";
            if (!text.trim()) return;
            const firstChar = text.charAt(0);
            const needSpace =
              lastChar !== "" &&
              !/[\s([{«„"'`]/.test(lastChar) &&
              !/[\s)\]}»"'`.,;:!?…]/.test(firstChar);
            const outText = needSpace ? " " + text : text;
            lastChar = outText.charAt(outText.length - 1);
            api.logger.info?.(
              `sellerclaw-ui: deliver block len=${outText.length} session_key=${sessionKey}`,
            );
            await postStreamDeltaBestEffort(api, account, sessionKey, outText);
          },
          onRecordError: (err: unknown) => {
            api.logger.error?.(`sellerclaw-ui: inbound session record error: ${String(err)}`);
          },
          onDispatchError: (err: unknown, info: { kind: string }) => {
            api.logger.error?.(`sellerclaw-ui: inbound ${info.kind} reply error: ${String(err)}`);
          },
        });
      })();

      void dispatchPromise
        .then(() => postStreamEndBestEffort(api, account, sessionKey))
        .catch((err: unknown) => {
          api.logger.error?.(`sellerclaw-ui: inbound dispatch failed: ${String(err)}`);
          void postStreamEndBestEffort(api, account, sessionKey);
        });

      return true;
    },
  });
}
