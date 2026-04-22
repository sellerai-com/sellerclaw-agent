import type { OpenClawPluginApi } from "openclaw/plugin-sdk/core";
import { dispatchInboundDirectDmWithRuntime } from "openclaw/plugin-sdk/channel-inbound";
import { readJsonWebhookBodyOrReject } from "openclaw/plugin-sdk/webhook-ingress";

import { resolveSellerclawUiAccount } from "./channel.js";
import { postOpenclawWebhook, type ScwUiAccount } from "./send.js";
import { getRuntime } from "./runtime-store.js";

interface InboundPayload {
  chat_id: string;
  agent_id: string;
  user_id: string;
  text: string;
  message_id?: string;
  /** Multimodal parts mirroring SellerClaw persisted raw_content; URLs forwarded as mediaUrls. */
  raw_content?: unknown[];
}

function extractMediaUrlsFromSellerclawRawContent(parts: unknown): string[] {
  if (!Array.isArray(parts)) return [];
  const urls: string[] = [];
  for (const part of parts) {
    if (!part || typeof part !== "object") continue;
    const p = part as Record<string, unknown>;
    if (p.type === "image_url" && p.image_url && typeof p.image_url === "object") {
      const url = (p.image_url as { url?: unknown }).url;
      if (typeof url === "string" && url.trim()) urls.push(url.trim());
    }
    if (p.type === "file_url" && p.file_url && typeof p.file_url === "object") {
      const url = (p.file_url as { url?: unknown }).url;
      if (typeof url === "string" && url.trim()) urls.push(url.trim());
    }
  }
  return urls;
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

      const mediaUrls = extractMediaUrlsFromSellerclawRawContent(payload.raw_content ?? []);
      const dispatchBase: Record<string, unknown> = {
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
        rawBody: payload.text.trim(),
        messageId: payload.message_id ?? crypto.randomUUID(),
        timestamp: Date.now(),
        commandAuthorized: true,
        deliver: async (replyPayload: unknown) => {
          const text =
            replyPayload && typeof replyPayload === "object" && "text" in replyPayload
              ? String((replyPayload as Record<string, unknown>).text ?? "")
              : "";
          if (!text.trim()) return;
          api.logger.info?.(
            `sellerclaw-ui: deliver block len=${text.length} session_key=${sessionKey}`,
          );
          await postStreamDeltaBestEffort(api, account, sessionKey, text);
        },
        onRecordError: (err: unknown) => {
          api.logger.error?.(`sellerclaw-ui: inbound session record error: ${String(err)}`);
        },
        onDispatchError: (err: unknown, info: { kind: string }) => {
          api.logger.error?.(`sellerclaw-ui: inbound ${info.kind} reply error: ${String(err)}`);
        },
      };
      if (payload.raw_content && Array.isArray(payload.raw_content) && payload.raw_content.length > 0) {
        dispatchBase.rawContent = payload.raw_content;
      }
      if (mediaUrls.length > 0) {
        dispatchBase.mediaUrls = mediaUrls;
        dispatchBase.mediaPaths = mediaUrls;
      }

      void dispatchInboundDirectDmWithRuntime(dispatchBase)
        .then(() => postStreamEndBestEffort(api, account, sessionKey))
        .catch((err: unknown) => {
          api.logger.error?.(`sellerclaw-ui: inbound dispatch failed: ${String(err)}`);
          void postStreamEndBestEffort(api, account, sessionKey);
        });

      return true;
    },
  });
}
