import { resolveInboundRouteEnvelopeBuilderWithRuntime } from "openclaw/plugin-sdk/inbound-envelope";
import { createChannelReplyPipeline } from "openclaw/plugin-sdk/channel-reply-pipeline";
import { runPreparedInboundReply } from "openclaw/plugin-sdk/channel-inbound";
import { normalizeOutboundReplyPayload } from "openclaw/plugin-sdk/reply-payload";

/**
 * Local re-implementation of OpenClaw's ``dispatchInboundDirectDmWithRuntime`` that ALSO forwards
 * the reasoning-stream callbacks (``onReasoningStream`` / ``onReasoningEnd``) into the reply run.
 *
 * Why this exists: the upstream convenience wrapper hardcodes ``replyOptions: { onModelSelected }``
 * and silently drops any caller-supplied reasoning callbacks, so streamed reasoning ("thinking")
 * never reaches a custom channel plugin. This copy is byte-for-byte the same orchestration as the
 * upstream wrapper EXCEPT it merges the caller's reasoning callbacks into ``replyOptions``. It is
 * composed entirely from PUBLIC ``openclaw/plugin-sdk/*`` building blocks — we do not patch or
 * monkey-patch OpenClaw; its own wrapper is left untouched, we just stop calling it.
 *
 * ⚠️ Drift risk: this mirrors OpenClaw-internal orchestration and the plugin-sdk shims are loosely
 * typed, so the compiler will NOT flag a mismatch. On every OpenClaw upgrade, re-verify this still
 * matches ``dispatchInboundDirectDmWithRuntime`` and smoke-test reasoning end-to-end.
 */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type DispatchParams = Record<string, any>;

export async function dispatchInboundDirectDmWithReasoning(params: DispatchParams): Promise<void> {
  const { route, buildEnvelope } = resolveInboundRouteEnvelopeBuilderWithRuntime({
    cfg: params.cfg,
    channel: params.channel,
    accountId: params.accountId,
    peer: params.peer,
    runtime: params.runtime.channel,
    sessionStore: params.cfg.session?.store,
  });
  const { storePath, body } = buildEnvelope({
    channel: params.channelLabel,
    from: params.conversationLabel,
    body: params.rawBody,
    timestamp: params.timestamp,
  });
  const ctxPayload = params.runtime.channel.reply.finalizeInboundContext({
    Body: body,
    BodyForAgent: params.bodyForAgent ?? params.rawBody,
    RawBody: params.rawBody,
    CommandBody: params.commandBody ?? params.rawBody,
    From: params.senderAddress,
    To: params.recipientAddress,
    SessionKey: route.sessionKey,
    AccountId: route.accountId ?? params.accountId,
    ChatType: "direct",
    ConversationLabel: params.conversationLabel,
    SenderId: params.senderId,
    Provider: params.provider ?? params.channel,
    Surface: params.surface ?? params.channel,
    MessageSid: params.messageId,
    MessageSidFull: params.messageId,
    Timestamp: params.timestamp,
    CommandAuthorized: params.commandAuthorized,
    OriginatingChannel: params.originatingChannel ?? params.channel,
    OriginatingTo: params.originatingTo ?? params.recipientAddress,
    ...params.extraContext,
  });
  const { onModelSelected, ...replyPipeline } = createChannelReplyPipeline({
    cfg: params.cfg,
    agentId: route.agentId,
    channel: params.channel,
    accountId: route.accountId ?? params.accountId,
  });
  await runPreparedInboundReply({
    channel: params.channel,
    accountId: route.accountId ?? params.accountId,
    routeSessionKey: route.sessionKey,
    storePath,
    ctxPayload,
    recordInboundSession: params.runtime.channel.session.recordInboundSession,
    record: { onRecordError: params.onRecordError },
    runDispatch: async () =>
      await params.runtime.channel.reply.dispatchReplyWithBufferedBlockDispatcher({
        ctx: ctxPayload,
        cfg: params.cfg,
        dispatcherOptions: {
          ...replyPipeline,
          // SECOND DIVERGENCE from upstream: forward the dispatcher's per-delivery info
          // (``{ kind: "block" | "final" | "tool", assistantMessageIndex }``) as the second
          // argument. The engine streams each reply block (``kind: "block"``) and then
          // re-delivers the whole reply once more as a consolidated final (``kind: "final"``);
          // without this label the bridge can't tell them apart and double-posts multi-block
          // replies. See ``inbound.ts`` deliver for how the label is consumed.
          deliver: async (payload: unknown, dispatchInfo?: unknown) => {
            const normalized =
              payload && typeof payload === "object" ? normalizeOutboundReplyPayload(payload) : {};
            return await params.deliver(normalized, dispatchInfo);
          },
          onError: params.onDispatchError,
        },
        // THE ONLY DIVERGENCE from upstream: upstream passes `replyOptions: { onModelSelected }`
        // and drops reasoning callbacks. We forward them so streamed reasoning reaches the channel.
        replyOptions: {
          onModelSelected,
          onReasoningStream: params.replyOptions?.onReasoningStream,
          onReasoningEnd: params.replyOptions?.onReasoningEnd,
        },
      }),
  });
}
