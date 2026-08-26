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
 * matches ``dispatchInboundDirectDmWithRuntime``, smoke-test reasoning end-to-end, and confirm an
 * aborted run still delivers its final with ``isError: true`` (see the third divergence below —
 * the delivery-timeline log line in ``inbound.ts`` names the branch it took, which is the canary).
 *
 * Verified against 2026.8.1-beta.2: upstream now assembles a ``ChannelTurnPlan`` and hands it to
 * ``dispatchRoutedChannelTurn`` instead of calling ``runPreparedInboundReply`` itself, but the plan
 * it builds still hardcodes ``replyOptions: { onModelSelected }`` — so reasoning callbacks are still
 * dropped and this copy still earns its keep. Every SDK entry point below survived that reshuffle
 * with its shape intact. Note ``plugin-sdk/channel-reply-pipeline`` is now deprecated in favour of
 * ``plugin-sdk/channel-outbound``; migrate when the old subpath stops shipping.
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
          // THIRD DIVERGENCE from upstream: keep ``isError``.
          //
          // ``normalizeOutboundReplyPayload`` extracts text and media only — by design, since
          // most channels just need something to send. But when a run aborts (run-budget
          // timeout, provider failure) OpenClaw does not throw: it *converts* the terminal
          // state into reply text ("LLM request timed out.") and marks that payload
          // ``isError: true`` (built in ``embedded-agent-runner/run/payloads.ts``, copied onto
          // the channel payload there, preserved through ``normalizeReplyPayload``, and used by
          // OpenClaw's own webchat to render a chat message as an error). Dropping the flag here
          // is what let a 22-character engine string be stored as the assistant's answer —
          // staging chat b76fd17a, 2026-08-19. ``inbound.ts`` consumes it to hold the string
          // back and close the turn honestly.
          deliver: async (payload: unknown, dispatchInfo?: unknown) => {
            const raw =
              payload && typeof payload === "object" ? (payload as Record<string, unknown>) : null;
            const normalized = raw ? normalizeOutboundReplyPayload(raw) : {};
            return await params.deliver(
              raw?.isError === true ? { ...normalized, isError: true } : normalized,
              dispatchInfo,
            );
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
