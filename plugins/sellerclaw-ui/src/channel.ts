import {
  createChannelPluginBase,
  createChatChannelPlugin,
} from "openclaw/plugin-sdk/core";
import type { OpenClawConfig } from "openclaw/plugin-sdk/core";

import {
  enqueueSend,
  postWebhookMessage,
  resolveOutboundExtId,
  uploadLocalMedia,
  type ScwUiAccount,
} from "./send.js";

export type { ScwUiAccount };

let _pluginConfig: OpenClawConfig | null = null;

export function setPluginConfig(cfg: OpenClawConfig): void {
  _pluginConfig = cfg;
}

function readSellerclawUiSection(cfg: OpenClawConfig): Record<string, unknown> | undefined {
  const channels = cfg.channels as Record<string, unknown> | undefined;
  const raw = channels?.["sellerclaw-ui"];
  return raw && typeof raw === "object" && !Array.isArray(raw)
    ? (raw as Record<string, unknown>)
    : undefined;
}

export function resolveSellerclawUiAccount(
  cfg: OpenClawConfig,
  _accountId?: string | null,
): ScwUiAccount {
  const section = readSellerclawUiSection(cfg);
  const apiBaseUrl = typeof section?.apiBaseUrl === "string" ? section.apiBaseUrl.trim() : "";
  const userId = typeof section?.userId === "string" ? section.userId.trim() : "";
  const internalWebhookSecret =
    typeof section?.internalWebhookSecret === "string"
      ? section.internalWebhookSecret.trim()
      : "";
  const localAgentBaseUrl =
    typeof section?.localAgentBaseUrl === "string"
      ? section.localAgentBaseUrl.trim()
      : "http://127.0.0.1:8001";
  if (!apiBaseUrl) {
    throw new Error("sellerclaw-ui: apiBaseUrl is required");
  }
  if (!userId) {
    throw new Error("sellerclaw-ui: userId is required");
  }
  if (!internalWebhookSecret) {
    throw new Error("sellerclaw-ui: internalWebhookSecret is required");
  }
  return { apiBaseUrl, userId, internalWebhookSecret, localAgentBaseUrl };
}

/**
 * Extract UUID chat_id from a channel address like "sellerclaw-ui:direct:{uuid}".
 * Returns null if the format doesn't match.
 */
export function extractChatIdFromAddress(address: string): string | null {
  const m = address.match(
    /^sellerclaw-ui:direct:([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$/i,
  );
  return m?.[1] ?? null;
}

export function resolveSessionKey(params: Record<string, unknown>): string | null {
  const top =
    (typeof params.sessionKey === "string" && params.sessionKey) ||
    (typeof params.session === "string" && params.session) ||
    (typeof params.to === "string" && params.to);
  if (top) {
    return top;
  }
  const delivery = params.delivery as Record<string, unknown> | undefined;
  if (delivery && typeof delivery.sessionKey === "string") {
    return delivery.sessionKey;
  }
  const ctx = params.context as Record<string, unknown> | undefined;
  if (ctx && typeof ctx.sessionKey === "string") {
    return ctx.sessionKey;
  }
  return null;
}

type OutboundParams = Record<string, unknown> & {
  account?: ScwUiAccount;
  config?: OpenClawConfig;
};

function resolveOutboundAccount(p: OutboundParams): ScwUiAccount {
  const account =
    p.account ??
    (p.config ? resolveSellerclawUiAccount(p.config) : null) ??
    (_pluginConfig ? resolveSellerclawUiAccount(_pluginConfig) : null);
  if (!account) {
    throw new Error("sellerclaw-ui: missing account/config in outbound params");
  }
  return account;
}

function inspectSellerclawUiAccount(
  cfg: OpenClawConfig,
  accountId?: string | null,
): {
  enabled: boolean;
  configured: boolean;
  tokenStatus: "available" | "missing";
} {
  try {
    resolveSellerclawUiAccount(cfg, accountId);
    return { enabled: true, configured: true, tokenStatus: "available" };
  } catch {
    return { enabled: false, configured: false, tokenStatus: "missing" };
  }
}

/** OpenClaw health / CLI expect `plugin.config.listAccountIds` (see gateway health snapshot). */
function listSellerclawUiAccountIds(cfg: OpenClawConfig): string[] {
  try {
    resolveSellerclawUiAccount(cfg);
    return ["default"];
  } catch {
    return [];
  }
}

function buildSellerclawUiRuntimeSnapshot(account: ScwUiAccount): {
  enabled: true;
  configured: true;
  running: true;
  connected: true;
  mode: "webhook";
} {
  void account;
  return {
    enabled: true,
    configured: true,
    running: true,
    connected: true,
    mode: "webhook",
  };
}

async function outboundSendText(params: unknown): Promise<{ messageId: string }> {
  const p = params as OutboundParams;
  if (p.silent) {
    return { messageId: "silent" };
  }
  const account = resolveOutboundAccount(p);
  const sessionKey = resolveSessionKey(p);
  if (!sessionKey) {
    throw new Error("sellerclaw-ui: missing session key on outbound sendText params");
  }
  const text = typeof p.text === "string" ? p.text : "";
  if (!text.trim()) {
    return { messageId: "empty" };
  }
  const chatId = extractChatIdFromAddress(sessionKey);
  return enqueueSend(sessionKey, () =>
    postWebhookMessage(account, sessionKey, {
      text,
      message_id: resolveOutboundExtId(p),
      ...(chatId ? { chat_id: chatId } : {}),
    }),
  );
}

function looksLikeLocalPath(value: string): boolean {
  return value.startsWith("/") || value.startsWith("file://");
}

function stripFilePrefix(value: string): string {
  return value.startsWith("file://") ? value.slice("file://".length) : value;
}

/**
 * Resolve the final public HTTPS image URL. If the caller supplies a local container path
 * (either via `imagePath`/`localImagePath`/`mediaPath` or via `imageUrl` pointing at
 * `/home/node/...` or `/tmp/...`), proxy-upload it through the agent so we get a real
 * download URL before delivery.
 */
async function resolveDeliverableImageUrl(
  account: ScwUiAccount,
  p: OutboundParams,
): Promise<string> {
  const explicitPath =
    (typeof p.imagePath === "string" && p.imagePath) ||
    (typeof p.localImagePath === "string" && p.localImagePath) ||
    (typeof p.mediaPath === "string" && p.mediaPath) ||
    "";
  const imageUrl = typeof p.imageUrl === "string" ? p.imageUrl.trim() : "";
  if (explicitPath) {
    const uploaded = await uploadLocalMedia(account, stripFilePrefix(explicitPath));
    return uploaded.downloadUrl;
  }
  if (!imageUrl) {
    throw new Error("sellerclaw-ui: imageUrl or imagePath is required for sendImage");
  }
  if (looksLikeLocalPath(imageUrl)) {
    const uploaded = await uploadLocalMedia(account, stripFilePrefix(imageUrl));
    return uploaded.downloadUrl;
  }
  return imageUrl;
}

async function outboundSendImage(params: unknown): Promise<{ messageId: string }> {
  const p = params as OutboundParams;
  const account = resolveOutboundAccount(p);
  const sessionKey = resolveSessionKey(p);
  if (!sessionKey) {
    throw new Error("sellerclaw-ui: missing session key on outbound sendImage params");
  }
  const imageUrl = await resolveDeliverableImageUrl(account, p);
  const caption = typeof p.text === "string" ? p.text : "";
  const rawContent: Record<string, unknown>[] = [];
  if (caption) {
    rawContent.push({ type: "text", text: caption });
  }
  rawContent.push({ type: "image_url", image_url: { url: imageUrl } });
  const chatId = extractChatIdFromAddress(sessionKey);
  return enqueueSend(sessionKey, () =>
    postWebhookMessage(account, sessionKey, {
      text: caption || imageUrl,
      raw_content: rawContent,
      message_id: resolveOutboundExtId(p),
      ...(chatId ? { chat_id: chatId } : {}),
    }),
  );
}

const sellerclawUiChatPlugin = createChatChannelPlugin<ScwUiAccount>({
  base: createChannelPluginBase({
    id: "sellerclaw-ui",
    capabilities: {
      chatTypes: ["direct", "agent"],
      reactions: false,
      threads: false,
      media: true,
      nativeCommands: false,
      blockStreaming: true,
    },
    config: {
      listAccountIds: listSellerclawUiAccountIds,
      resolveAccount: resolveSellerclawUiAccount,
      inspectAccount: inspectSellerclawUiAccount,
    },
    setup: {
      resolveAccount: resolveSellerclawUiAccount,
      inspectAccount: inspectSellerclawUiAccount,
    },
  }),
  security: {
    dm: {
      channelKey: "sellerclaw-ui",
      resolvePolicy: () => "open",
      resolveAllowFrom: () => [],
      defaultPolicy: "open",
    },
  },
  threading: { topLevelReplyToMode: "reply" },
  outbound: {
    sendText: outboundSendText,
    sendImage: outboundSendImage,
    attachedResults: {
      sendText: outboundSendText,
      sendImage: outboundSendImage,
    },
  },
});

export const sellerclawUiChannelPlugin = {
  ...sellerclawUiChatPlugin,
  status: {
    defaultRuntime: {
      running: true,
      connected: true,
      mode: "webhook",
    },
    buildAccountSnapshot: async ({ account }: { account: ScwUiAccount }) =>
      buildSellerclawUiRuntimeSnapshot(account),
  },
};
