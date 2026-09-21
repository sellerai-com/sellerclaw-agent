import {
  buildChannelOutboundSessionRoute,
  createChannelPluginBase,
  createChatChannelPlugin,
} from "openclaw/plugin-sdk/core";
import type { ChannelOutboundSessionRoute, OpenClawConfig } from "openclaw/plugin-sdk/core";

import { questionForReplyTarget, recordReplyDelivered } from "./owner-turns.js";
import {
  enqueueSend,
  postTurnEnd,
  postTurnPart,
  postTurnStart,
  resolveMediaKind,
  resolveOutboundMediaUrl,
  type ScwUiAccount,
  type TurnPairing,
} from "./send.js";
import { getSharedState } from "./shared-state.js";

export type { ScwUiAccount };

/**
 * The config the plugin was registered with, for the outbound paths that get none of their own.
 *
 * In the process-wide slot rather than a module variable: this file is loaded twice over. OpenClaw
 * re-evaluates plugin modules on every registry pass, and the bundled channel entry pulls this
 * module in through a loader boundary of its own — so the instance that answered
 * ``setPluginConfig`` need not be the instance the outbound adapter came from.
 */
const pluginConfigSlot = getSharedState("channel:plugin-config", () => ({
  value: null as OpenClawConfig | null,
}));

export function setPluginConfig(cfg: OpenClawConfig): void {
  pluginConfigSlot.value = cfg;
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
  const agentApiKey = typeof section?.agentApiKey === "string" ? section.agentApiKey.trim() : "";
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
  if (!agentApiKey) {
    throw new Error("sellerclaw-ui: agentApiKey is required");
  }
  if (!internalWebhookSecret) {
    throw new Error("sellerclaw-ui: internalWebhookSecret is required");
  }
  return {
    apiBaseUrl,
    userId,
    agentApiKey,
    internalWebhookSecret,
    localAgentBaseUrl,
  };
}

/**
 * Extract UUID chat_id from a channel address like "sellerclaw-ui:direct:{uuid}".
 * Returns null if the format doesn't match.
 */
export function extractChatIdFromAddress(address: string): string | null {
  const m = address.match(SELLERCLAW_UI_DIRECT_TARGET_RE);
  return m?.[1] ?? null;
}

/**
 * Address shape this plugin uses for direct chats:
 * ``sellerclaw-ui:direct:<uuid>``. The ``message`` tool's default target (no
 * explicit ``to`` from the agent) is resolved from the active session's
 * inbound address — that string matches this regex, but OpenClaw's generic
 * ``looksLikeTargetId`` only accepts ``channel:|group:|user:|@|#`` prefixes
 * out of the box. Without a channel-specific resolver the runtime drops into
 * directory lookup (we don't expose a directory) and raises ``Unknown
 * target`` even though the address is internally valid. Surfaced as
 * ``messaging.targetResolver.looksLikeId`` below.
 */
export const SELLERCLAW_UI_DIRECT_TARGET_RE =
  /^sellerclaw-ui:direct:([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$/i;

export function looksLikeSellerclawUiTarget(raw: string): boolean {
  return normalizeSellerclawUiTarget(raw) !== null;
}

/** A chat id on its own — what the agent reads out of an address and often passes back as-is. */
const BARE_CHAT_ID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/**
 * The canonical ``sellerclaw-ui:direct:<chat_id>`` address for whatever the agent aimed at.
 *
 * The ``message`` tool's ``to`` is free-form, and the agent reliably reaches for the shortest
 * thing that identifies the chat — the bare id, or the session key it sees in its own context —
 * rather than the address the resolver wants. Every such call used to die with ``Unknown target
 * "<uuid>" for sellerclaw-ui``, costing a model round trip before the agent guessed the longer
 * form. Accepting all three spellings here (and normalizing before delivery, so the webhook still
 * carries a proper ``chat_id``) turns that class of failure into a no-op.
 */
export function normalizeSellerclawUiTarget(raw: string): string | null {
  const trimmed = raw.trim();
  if (SELLERCLAW_UI_DIRECT_TARGET_RE.test(trimmed)) return trimmed;
  if (BARE_CHAT_ID_RE.test(trimmed)) return `sellerclaw-ui:direct:${trimmed}`;
  return extractTargetFromSessionKey(trimmed);
}

/**
 * The bare chat id behind any outbound target spelling this channel accepts.
 *
 * Covers the canonical address, the bare uuid, a full session key — everything
 * ``normalizeSellerclawUiTarget`` takes — plus the kind-prefixed remnants other layers
 * produce: ``direct:<uuid>`` when a caller has already stripped the channel prefix, and
 * ``user:<uuid>`` / ``dm:<uuid>`` as core's generic fallback wrote into stored session routes.
 */
export function chatIdFromOutboundTarget(raw: string): string | null {
  const address = normalizeSellerclawUiTarget(raw);
  if (address) return extractChatIdFromAddress(address);
  const stripped = raw.trim().replace(/^(direct|user|dm):/i, "");
  return BARE_CHAT_ID_RE.test(stripped) ? stripped : null;
}

/**
 * Same address, unanchored: session keys carry it as a suffix
 * (``agent:<agentId>:sellerclaw-ui:direct:<chat_id>``), so lifecycle hooks — which see a
 * session key, never a bare target — can recover the chat this run replies into.
 */
const SELLERCLAW_UI_SESSION_KEY_RE =
  /sellerclaw-ui:direct:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i;

/** Extract the ``sellerclaw-ui:direct:<chat_id>`` target embedded in a session key. */
export function extractTargetFromSessionKey(sessionKey: string): string | null {
  return sessionKey.match(SELLERCLAW_UI_SESSION_KEY_RE)?.[0] ?? null;
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

/**
 * The chat an outbound send is aimed at, in the single spelling the rest of the path uses.
 *
 * Every outbound entry point goes through here so a new one cannot quietly skip normalization
 * and reintroduce ``Unknown target`` (or, worse, a turn posted without a ``chat_id``).
 */
function resolveOutboundDestination(
  p: OutboundParams,
  sendKind: string,
): { address: string; chatId: string | null } {
  const raw = resolveSessionKey(p);
  if (!raw) {
    throw new Error(`sellerclaw-ui: missing session key on outbound ${sendKind} params`);
  }
  const address = normalizeSellerclawUiTarget(raw) ?? raw;
  return { address, chatId: extractChatIdFromAddress(address) };
}

/**
 * When each chat last received a message on the outbound road, by chat id.
 *
 * A live chat turn that answers through the ``message`` tool delivers nothing through its own
 * dispatch, so the turn cannot tell that answer from silence by what it delivered itself. This is
 * how it tells: a send into its chat after it started means the owner has something to read (see
 * ``resolveTurnEnd`` in ``inbound.ts``). Process-wide, because the outbound adapter and the inbound
 * route need not come from the same evaluation of this module.
 */
const lastOutboundDeliveryAt = getSharedState(
  "channel:last-outbound-delivery",
  () => new Map<string, number>(),
);

/** How long an entry is kept; a live turn only ever asks about the minutes it has been running. */
const OUTBOUND_DELIVERY_MEMORY_MS = 60 * 60_000;

function recordOutboundDelivery(chatId: string | null): void {
  if (!chatId) return;
  const now = Date.now();
  for (const [key, at] of lastOutboundDeliveryAt) {
    if (now - at > OUTBOUND_DELIVERY_MEMORY_MS) lastOutboundDeliveryAt.delete(key);
  }
  lastOutboundDeliveryAt.set(chatId, now);
}

/** Whether a message reached this chat on the outbound road at or after ``since`` (epoch ms). */
export function outboundDeliveredSince(chatId: string, since: number): boolean {
  return (lastOutboundDeliveryAt.get(chatId) ?? 0) >= since;
}

/** Test-only: forget every recorded outbound delivery. */
export function __resetOutboundDeliveries(): void {
  lastOutboundDeliveryAt.clear();
}

/** One outbound part with the ``part_id`` filled in by {@link deliverOutboundAsParts}. */
type OutboundPartInput =
  | { kind: "text"; text: string }
  | { kind: "image" | "file"; url: string; filename?: string; content_type?: string };

function resolveOutboundAccount(p: OutboundParams): ScwUiAccount {
  const account =
    p.account ??
    (p.config ? resolveSellerclawUiAccount(p.config) : null) ??
    (pluginConfigSlot.value ? resolveSellerclawUiAccount(pluginConfigSlot.value) : null);
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

/**
 * Deliver a one-shot outbound message (``message`` tool / proactive / handoff) as a
 * self-contained parts turn: start → part(s) → end. Each outbound send is its own
 * assistant message, so async completions never collide with a streamed turn.
 *
 * The reasoning the owner watched while this answer was written lives in the dispatch's own
 * message, which stays empty when the agent answers with the ``message`` tool. Reuniting the two
 * is the cloud's job: it drops that empty message at ``turn/end`` and hands the blocks to this
 * exchange's answer, i.e. the last message the turn produced. Doing it here instead — delivering
 * into the dispatch's message — would pin the panel to the *first* send of a turn, which on a
 * multi-message turn is an interim note rather than the answer.
 */
async function deliverOutboundAsParts(
  account: ScwUiAccount,
  sessionKey: string,
  chatId: string | null,
  parts: OutboundPartInput[],
  pairing: TurnPairing,
): Promise<{ messageId: string }> {
  const messageId = crypto.randomUUID();
  await postTurnStart(account, sessionKey, messageId, chatId, pairing);
  for (const part of parts) {
    await postTurnPart(
      account,
      sessionKey,
      messageId,
      { part_id: crypto.randomUUID(), ...part },
      chatId,
    );
  }
  await postTurnEnd(account, sessionKey, messageId, chatId);
  recordOutboundDelivery(chatId);
  if ("replyTo" in pairing) recordReplyDelivered(pairing.replyTo);
  return { messageId };
}

/**
 * What an outbound send answers, from the reply target the engine put on it.
 *
 * The channel runs in reply mode ``all`` (``threading`` below), so every send a run makes while
 * answering an owner message carries that message's id — the ``message`` tool's, and a queued
 * run's answer routed back to its chat. A send without one comes from a run that answers nobody:
 * a subagent report, a cron delivery, the restart notice. Said outright, so the cloud does not
 * pin it to whichever of the owner's messages happens to be waiting.
 */
/**
 * Whether a ``message`` send the agent addressed explicitly goes to the chat it is answering in.
 *
 * The engine puts the owner's message on a send only when the send stays in the current
 * conversation, and an explicit target has to be recognised as that conversation by the channel.
 * The agent names its own chat in every spelling ``chatIdFromOutboundTarget`` accepts — the bare
 * id most of all — so without this its interim notes ("On it…") went out as unprompted updates.
 */
export function matchesCurrentChat(params: {
  target: string;
  toolContext: { currentMessagingTarget?: string; currentChannelId?: string };
}): boolean {
  const current =
    chatIdFromOutboundTarget(params.toolContext.currentMessagingTarget ?? "") ??
    chatIdFromOutboundTarget(params.toolContext.currentChannelId ?? "");
  const aimed = chatIdFromOutboundTarget(params.target);
  return Boolean(current && aimed && current.toLowerCase() === aimed.toLowerCase());
}

function pairingForOutbound(p: OutboundParams): TurnPairing {
  const questionId = questionForReplyTarget(p.replyToId);
  return questionId ? { replyTo: questionId } : { unprompted: true };
}

/**
 * Post one text answer into a chat as its own assistant message, bypassing the runtime.
 *
 * Used by the completion-delivery rescue: when a completion run's answer is written but the
 * runtime never asks us to deliver it, the plugin sends it itself. Same road as an outbound
 * ``message`` send (queued per session, one parts turn), so ordering against a live stream
 * holds and the cloud dedupes by ``message_id`` as usual.
 */
export async function deliverTextToChat(
  account: ScwUiAccount,
  sessionKey: string,
  text: string,
): Promise<{ messageId: string }> {
  // Fold the caller's session key down to the chat's address, the same key ordinary ``message``
  // sends queue under: queued under the longer form this rescue would sit in a queue of its own
  // and could interleave with the very stream it is meant to follow.
  const address = normalizeSellerclawUiTarget(sessionKey) ?? sessionKey;
  const chatId = extractChatIdFromAddress(address);
  // A completion run's answer: it answers no owner message of its own.
  return enqueueSend(address, () =>
    deliverOutboundAsParts(account, address, chatId, [{ kind: "text", text }], {
      unprompted: true,
    }),
  );
}

async function outboundSendText(params: unknown): Promise<{ messageId: string }> {
  const p = params as OutboundParams;
  if (p.silent) {
    return { messageId: "silent" };
  }
  const account = resolveOutboundAccount(p);
  const { address, chatId } = resolveOutboundDestination(p, "sendText");
  const text = typeof p.text === "string" ? p.text : "";
  if (!text.trim()) {
    return { messageId: "empty" };
  }
  const pairing = pairingForOutbound(p);
  return enqueueSend(address, () =>
    deliverOutboundAsParts(account, address, chatId, [{ kind: "text", text }], pairing),
  );
}

/**
 * Resolve the final public HTTPS image URL. If the caller supplies a local container path
 * (either via `imagePath`/`localImagePath`/`mediaPath` or via `imageUrl` pointing at
 * `/home/node/...` or `/tmp/...`), proxy-upload it through the agent so we get a real
 * download URL before delivery.
 */
async function resolveDeliverableImage(
  account: ScwUiAccount,
  p: OutboundParams,
): Promise<{ url: string; contentType: string }> {
  const explicitPath =
    (typeof p.imagePath === "string" && p.imagePath) ||
    (typeof p.localImagePath === "string" && p.localImagePath) ||
    (typeof p.mediaPath === "string" && p.mediaPath) ||
    "";
  const imageUrl = typeof p.imageUrl === "string" ? p.imageUrl.trim() : "";
  const source = explicitPath || imageUrl;
  if (!source) {
    throw new Error("sellerclaw-ui: imageUrl or imagePath is required for sendImage");
  }
  return resolveOutboundMediaUrl(account, source);
}

async function outboundSendImage(params: unknown): Promise<{ messageId: string }> {
  const p = params as OutboundParams;
  const account = resolveOutboundAccount(p);
  const { address, chatId } = resolveOutboundDestination(p, "sendImage");
  const { url: imageUrl, contentType } = await resolveDeliverableImage(account, p);
  const caption = typeof p.text === "string" ? p.text : "";
  const parts: OutboundPartInput[] = [];
  if (caption.trim()) {
    parts.push({ kind: "text", text: caption });
  }
  parts.push({
    kind: "image",
    url: imageUrl,
    ...(contentType ? { content_type: contentType } : {}),
  });
  const pairing = pairingForOutbound(p);
  return enqueueSend(address, () =>
    deliverOutboundAsParts(account, address, chatId, parts, pairing),
  );
}

/**
 * Generic media delivery for OpenClaw's normalized outbound pipeline
 * (``infra/outbound/deliver`` → ``createPluginHandler``). Unlike
 * {@link outboundSendImage} — which the ``message`` tool invokes with an
 * ``imageUrl``/``imagePath`` — the runtime calls ``sendMedia`` with a
 * ``{ text: caption, mediaUrl }`` context whenever a reply or completion
 * carries media. The requester-agent handoff that delivers background
 * ``image_generate`` results takes exactly this path, so without ``sendMedia``
 * the runtime logs "outbound adapter does not implement sendMedia; media URLs
 * will be dropped" and the user only sees the caption text.
 *
 * Mirrors the inbound ``deliver`` media branch (see ``inbound.ts``): local
 * container artifacts are proxy-uploaded to a public URL, then delivered with
 * the same ``raw_content`` shape so images and files render identically
 * regardless of which path produced them.
 */
async function outboundSendMedia(params: unknown): Promise<{ messageId: string }> {
  const p = params as OutboundParams;
  if (p.silent) {
    return { messageId: "silent" };
  }
  const account = resolveOutboundAccount(p);
  const { address, chatId } = resolveOutboundDestination(p, "sendMedia");
  const rawMediaUrl = typeof p.mediaUrl === "string" ? p.mediaUrl.trim() : "";
  if (!rawMediaUrl) {
    throw new Error("sellerclaw-ui: mediaUrl is required for sendMedia");
  }
  const caption = typeof p.text === "string" ? p.text : "";
  const { url, contentType } = await resolveOutboundMediaUrl(account, rawMediaUrl);
  const parts: OutboundPartInput[] = [];
  if (caption.trim()) {
    parts.push({ kind: "text", text: caption });
  }
  parts.push({
    kind: resolveMediaKind(url, contentType),
    url,
    ...(contentType ? { content_type: contentType } : {}),
  });
  const pairing = pairingForOutbound(p);
  return enqueueSend(address, () =>
    deliverOutboundAsParts(account, address, chatId, parts, pairing),
  );
}

const sellerclawUiChatPlugin = createChatChannelPlugin<ScwUiAccount>({
  base: createChannelPluginBase({
    id: "sellerclaw-ui",
    capabilities: {
      // OpenClaw validates this list against a closed set (direct | group | channel | thread) and
      // rejects the WHOLE channel registration when anything else appears in it — silently, as one
      // startup diagnostic. "agent" was never one of those values; it only survived because older
      // runtimes did not check. On 2026.9.2 it cost every outbound send: the channel stayed in the
      // config, its plugin was dropped from the catalog, and the message tool answered "Outbound
      // not configured for channel: sellerclaw-ui" for a whole afternoon of finished work nobody
      // was told about. Every chat we address is a direct one, here and on the inbound side.
      chatTypes: ["direct"],
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
  // Every send a run makes carries the owner message it answers (``pairingForOutbound``). Reply
  // mode ``all`` puts it on the ``message`` tool's sends; ``resolveReplyTransport`` on a queued
  // run's answer, which the engine routes back to the chat with that message as its current one.
  // (The ``topLevelReplyToMode`` shorthand this replaced names a *channel* whose config holds the
  // mode — ``"reply"`` pointed at no channel, which silently meant ``off``.)
  threading: {
    resolveReplyToMode: () => "all",
    resolveReplyTransport: (params: { replyToId?: string | null; currentMessageId?: string }) => ({
      replyToId: params.replyToId ?? params.currentMessageId ?? null,
    }),
    matchesToolContextTarget: matchesCurrentChat,
  },
  outbound: {
    sendText: outboundSendText,
    sendImage: outboundSendImage,
    sendMedia: outboundSendMedia,
    attachedResults: {
      sendText: outboundSendText,
      sendImage: outboundSendImage,
      sendMedia: outboundSendMedia,
    },
  },
});

export const sellerclawUiChannelPlugin = {
  ...sellerclawUiChatPlugin,
  /**
   * OpenClaw's outbound target resolver (``infra/outbound/target-resolver.ts``)
   * uses three messaging hooks to turn the agent's free-form ``to`` argument
   * — or the session's default inbound address when ``to`` is omitted — into
   * a concrete delivery target:
   *
   *   - ``inferTargetChatType``: picks the directory ``kind`` (``user`` /
   *     ``group`` / ``channel``). We always run as direct DMs, so ``"direct"``.
   *   - ``targetResolver.looksLikeId``: declares a raw address as a valid
   *     opaque id so the resolver skips its directory-lookup branch (we don't
   *     publish a peer/group directory — every chat is identified by the
   *     ``chat_id`` UUID baked into the address).
   *   - ``targetResolver.hint``: human-readable hint surfaced in
   *     ``Unknown target`` / ``Ambiguous target`` errors when a malformed
   *     address still gets through.
   *
   * Without ``looksLikeId`` the resolver falls through to a directory query,
   * gets an empty list, and raises ``Unknown target
   * "sellerclaw-ui:direct:<uuid>" for sellerclaw-ui`` — even though that
   * address is exactly the one the active session is bound to. Symptom in
   * the wild: the ``message`` tool throws on every call, the agent burns
   * retries, and the failure surfaces as a ``Message failed`` badge stacked
   * against the agent's text reply.
   */
  messaging: {
    inferTargetChatType: ({ to }: { to: string }): "direct" | undefined =>
      looksLikeSellerclawUiTarget(to) ? "direct" : undefined,
    /**
     * The canonical address for whatever spelling of a chat the agent used. The engine compares a
     * ``message`` send's target with the chat a subagent's report is owed to through this, and a
     * bare chat id is not equal to the address: the report counted as never delivered, and the
     * engine re-ran the report run up to twice more — extra model calls, and a second copy of the
     * report whenever a re-run wrote one.
     */
    normalizeTarget: (raw: string): string | undefined =>
      normalizeSellerclawUiTarget(raw) ?? undefined,
    targetResolver: {
      hint: "Expected sellerclaw-ui:direct:<chat_id-uuid>, or the bare chat id.",
      looksLikeId: (raw: string) => looksLikeSellerclawUiTarget(raw),
    },
    /**
     * Session route for mirroring an outbound send back into the sending session's
     * transcript. Without this hook, core's fallback (``resolveFallbackSession``) strips only
     * the channel prefix from the target and its ``stripKindPrefix`` does not know
     * ``direct:`` — so ``sellerclaw-ui:direct:<uuid>`` became peer id ``direct:<uuid>`` and
     * the mirror aimed at ``agent:…:sellerclaw-ui:direct:direct:<uuid>``, a session that does
     * not exist. Every ``message``-tool send then logged ``failed to mirror outbound delivery
     * … session rebound`` and never reached the agent's own history: on its next run the agent
     * had no record of having spoken, which is a straight path to telling the owner the same
     * thing twice. The peer id here is the bare chat uuid, so the mirror lands in the same
     * session inbound delivery uses — which is also why ``recipientSessionExact`` is safe.
     */
    resolveOutboundSessionRoute: (params: {
      cfg: OpenClawConfig;
      agentId: string;
      accountId?: string | null;
      target: string;
    }): ChannelOutboundSessionRoute | null => {
      const chatId = chatIdFromOutboundTarget(params.target)?.toLowerCase();
      if (!chatId) return null;
      return buildChannelOutboundSessionRoute({
        cfg: params.cfg,
        agentId: params.agentId,
        channel: "sellerclaw-ui",
        accountId: params.accountId,
        recipientSessionExact: true,
        peer: { kind: "direct", id: chatId },
        chatType: "direct",
        from: `sellerclaw-ui:${chatId}`,
        to: `sellerclaw-ui:direct:${chatId}`,
      });
    },
  },
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
