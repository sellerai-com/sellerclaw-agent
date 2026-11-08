import { beforeEach, describe, expect, it, vi } from "vitest";

const {
  resolveAgentRouteMock,
  envelopeBuilderMock,
  formatEnvelopeMock,
  createPipelineMock,
  runPreparedMock,
  normalizeMock,
} = vi.hoisted(() => ({
  resolveAgentRouteMock: vi.fn(),
  envelopeBuilderMock: vi.fn(),
  formatEnvelopeMock: vi.fn(),
  createPipelineMock: vi.fn(),
  runPreparedMock: vi.fn(),
  // Like the real plugin-sdk normalizer, drops ``isError`` (it extracts text/media fields
  // only) — the wrapper under test is what puts the flag back.
  normalizeMock: vi.fn((p: Record<string, unknown>) => {
    const { isError: _dropped, ...rest } = p;
    return rest;
  }),
}));

vi.mock("openclaw/plugin-sdk/routing", () => ({
  resolveAgentRoute: (...a: unknown[]) => resolveAgentRouteMock(...a),
}));
vi.mock("openclaw/plugin-sdk/channel-outbound", () => ({
  createChannelMessageReplyPipeline: (...a: unknown[]) => createPipelineMock(...a),
}));
vi.mock("openclaw/plugin-sdk/channel-inbound", () => ({
  runPreparedInboundReply: (...a: unknown[]) => runPreparedMock(...a),
  createChannelInboundEnvelopeBuilderAsync: (...a: unknown[]) => envelopeBuilderMock(...a),
}));
vi.mock("openclaw/plugin-sdk/reply-payload", () => ({
  normalizeOutboundReplyPayload: (p: unknown) => normalizeMock(p as Record<string, unknown>),
}));

import { dispatchInboundDirectDmWithReasoning } from "../inbound-reply-with-reasoning.js";

describe("dispatchInboundDirectDmWithReasoning", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    resolveAgentRouteMock.mockReturnValue({
      sessionKey: "sk",
      agentId: "supervisor",
      accountId: "default",
    });
    formatEnvelopeMock.mockReturnValue("envelope-body");
    envelopeBuilderMock.mockResolvedValue(formatEnvelopeMock);
  });

  it("routes the message, builds its envelope with the awaited builder and records into the agent's store", async () => {
    createPipelineMock.mockReturnValue({ onModelSelected: vi.fn() });
    runPreparedMock.mockResolvedValue(undefined);
    const resolveStorePath = vi.fn(() => "/state/agents/supervisor/sessions");
    const recordInboundSession = vi.fn();
    const cfg = { session: { store: "store" } };
    const runtime = {
      channel: {
        reply: {
          finalizeInboundContext: vi.fn((c: unknown) => c),
          dispatchReplyWithBufferedBlockDispatcher: vi.fn(),
        },
        session: { recordInboundSession, resolveStorePath },
      },
    };

    await dispatchInboundDirectDmWithReasoning({
      cfg,
      runtime,
      channel: "sellerclaw-ui",
      channelLabel: "SellerClaw UI",
      accountId: "default",
      peer: { kind: "direct", id: "c1" },
      senderId: "u1",
      senderAddress: "sellerclaw-ui:u1",
      recipientAddress: "sellerclaw-ui:direct:c1",
      conversationLabel: "c1",
      rawBody: "hi",
      messageId: "m1",
      timestamp: 123,
      commandAuthorized: true,
      deliver: vi.fn(),
    });

    const route = { sessionKey: "sk", agentId: "supervisor", accountId: "default" };
    expect(resolveAgentRouteMock).toHaveBeenCalledWith({
      cfg,
      channel: "sellerclaw-ui",
      accountId: "default",
      peer: { kind: "direct", id: "c1" },
    });
    expect(resolveStorePath).toHaveBeenCalledWith("store", { agentId: "supervisor" });
    expect(envelopeBuilderMock).toHaveBeenCalledWith({ cfg, route });
    expect(formatEnvelopeMock).toHaveBeenCalledWith({
      channel: "SellerClaw UI",
      from: "c1",
      body: "hi",
      timestamp: 123,
    });
    expect(runPreparedMock).toHaveBeenCalledTimes(1);
    const prepared = runPreparedMock.mock.calls[0]![0] as Record<string, unknown>;
    expect(prepared).toMatchObject({
      channel: "sellerclaw-ui",
      accountId: "default",
      routeSessionKey: "sk",
      storePath: "/state/agents/supervisor/sessions",
      recordInboundSession,
    });
    expect((prepared.ctxPayload as Record<string, unknown>).Body).toBe("envelope-body");
  });

  it("forwards onReasoningStream/onReasoningEnd into the reply dispatcher's replyOptions", async () => {
    const onModelSelected = vi.fn();
    createPipelineMock.mockReturnValue({ onModelSelected, pipelineField: "x" });

    // runPreparedInboundReply wraps the actual dispatch; run it so we capture the inner call.
    runPreparedMock.mockImplementation(async (p: { runDispatch: () => Promise<void> }) => {
      await p.runDispatch();
    });

    const dispatchReplyMock = vi.fn().mockResolvedValue(undefined);
    const channelDispatch = vi.fn();
    const runtime = {
      channel: {
        reply: {
          finalizeInboundContext: vi.fn((c: unknown) => ({ ctx: c })),
          dispatchReplyWithBufferedBlockDispatcher: dispatchReplyMock,
          dispatchReplyFromConfig: channelDispatch,
        },
        session: { recordInboundSession: vi.fn(), resolveStorePath: vi.fn(() => "/store") },
      },
    };

    const onReasoningStream = vi.fn();
    const onReasoningEnd = vi.fn();
    const abortSignal = new AbortController().signal;
    const turnAdoptionLifecycle = { onAdopted: vi.fn(), onSettled: vi.fn() };

    await dispatchInboundDirectDmWithReasoning({
      cfg: { session: { store: "store" } },
      runtime,
      channel: "sellerclaw-ui",
      channelLabel: "SellerClaw UI",
      accountId: "default",
      peer: { kind: "direct", id: "c1" },
      senderId: "u1",
      senderAddress: "sellerclaw-ui:u1",
      recipientAddress: "sellerclaw-ui:direct:c1",
      conversationLabel: "c1",
      rawBody: "hi",
      messageId: "m1",
      timestamp: 123,
      commandAuthorized: true,
      replyOptions: { onReasoningStream, onReasoningEnd, abortSignal, turnAdoptionLifecycle },
      deliver: vi.fn().mockResolvedValue(undefined),
    });

    expect(dispatchReplyMock).toHaveBeenCalledTimes(1);
    const callArg = dispatchReplyMock.mock.calls[0]![0] as {
      replyOptions: Record<string, unknown>;
      dispatchReplyFromConfig: unknown;
    };
    // The whole point of the local re-implementation: reasoning callbacks survive into replyOptions
    // (upstream dropped them, passing only onModelSelected).
    expect(callArg.replyOptions).toEqual({
      onModelSelected,
      onReasoningStream,
      onReasoningEnd,
      abortSignal,
      turnAdoptionLifecycle,
    });
    // The channel runtime's dispatch, which lets a mid-run message reach the ``steer`` queue mode
    // instead of waiting for the run to end.
    expect(callArg.dispatchReplyFromConfig).toBe(channelDispatch);
  });

  it("asks the engine for tool starts when the caller listens for them", async () => {
    createPipelineMock.mockReturnValue({ onModelSelected: vi.fn() });
    runPreparedMock.mockImplementation(async (p: { runDispatch: () => Promise<void> }) => {
      await p.runDispatch();
    });
    const dispatchReplyMock = vi.fn().mockResolvedValue(undefined);
    const runtime = {
      channel: {
        reply: {
          finalizeInboundContext: vi.fn((c: unknown) => ({ ctx: c })),
          dispatchReplyWithBufferedBlockDispatcher: dispatchReplyMock,
          dispatchReplyFromConfig: vi.fn(),
        },
        session: { recordInboundSession: vi.fn(), resolveStorePath: vi.fn(() => "/store") },
      },
    };
    const onToolStart = vi.fn();

    await dispatchInboundDirectDmWithReasoning({
      cfg: { session: { store: "store" } },
      runtime,
      channel: "sellerclaw-ui",
      channelLabel: "SellerClaw UI",
      accountId: "default",
      peer: { kind: "direct", id: "c1" },
      senderId: "u1",
      senderAddress: "sellerclaw-ui:u1",
      recipientAddress: "sellerclaw-ui:direct:c1",
      conversationLabel: "c1",
      rawBody: "hi",
      messageId: "m1",
      timestamp: 123,
      commandAuthorized: true,
      replyOptions: { onToolStart },
      deliver: vi.fn().mockResolvedValue(undefined),
    });

    const callArg = dispatchReplyMock.mock.calls[0]![0] as { replyOptions: Record<string, unknown> };
    // Without the second flag the engine keeps tool starts to channels that show tool summaries.
    expect(callArg.replyOptions).toMatchObject({
      onToolStart,
      allowToolLifecycleWhenProgressHidden: true,
    });
  });

  it.each([
    {
      name: "an authorized sender",
      inboundAccessAuthorized: true,
      access: { InboundAccessAuthorized: true, ConversationRouteContextObserved: true },
    },
    { name: "a system turn", inboundAccessAuthorized: undefined, access: {} },
    { name: "a non-true flag", inboundAccessAuthorized: "true", access: {} },
  ])(
    "builds the inbound context upstream builds, for $name",
    async ({ inboundAccessAuthorized, access }) => {
      createPipelineMock.mockReturnValue({ onModelSelected: vi.fn() });
      runPreparedMock.mockResolvedValue(undefined);
      const finalizeInboundContext = vi.fn((c: unknown) => c);
      const runtime = {
        channel: {
          reply: { finalizeInboundContext, dispatchReplyWithBufferedBlockDispatcher: vi.fn() },
          session: { recordInboundSession: vi.fn(), resolveStorePath: vi.fn(() => "/store") },
        },
      };

      await dispatchInboundDirectDmWithReasoning({
        cfg: { session: { store: "store" } },
        runtime,
        channel: "sellerclaw-ui",
        channelLabel: "SellerClaw UI",
        accountId: "default",
        peer: { kind: "direct", id: "c1" },
        senderId: "u1",
        senderAddress: "sellerclaw-ui:u1",
        recipientAddress: "sellerclaw-ui:direct:c1",
        conversationLabel: "c1",
        rawBody: "hi",
        messageId: "m1",
        timestamp: 123,
        commandAuthorized: true,
        inboundAccessAuthorized,
        extraContext: { Effort: "high" },
        deliver: vi.fn(),
      });

      expect(finalizeInboundContext).toHaveBeenCalledWith({
        Body: "envelope-body",
        BodyForAgent: "hi",
        RawBody: "hi",
        CommandBody: "hi",
        From: "sellerclaw-ui:u1",
        To: "sellerclaw-ui:direct:c1",
        SessionKey: "sk",
        AccountId: "default",
        ChatType: "direct",
        ConversationLabel: "c1",
        SenderId: "u1",
        Provider: "sellerclaw-ui",
        Surface: "sellerclaw-ui",
        MessageSid: "m1",
        MessageSidFull: "m1",
        Timestamp: 123,
        CommandAuthorized: true,
        ...access,
        ConversationRoutePeerId: "c1",
        OriginatingChannel: "sellerclaw-ui",
        // Ours, not upstream's: the reply's origin is the chat, not the sender.
        OriginatingTo: "sellerclaw-ui:direct:c1",
        NativeDirectUserId: "c1",
        Effort: "high",
      });
    },
  );

  it("forwards the dispatcher's per-delivery info (kind/index) as the second deliver arg", async () => {
    createPipelineMock.mockReturnValue({ onModelSelected: vi.fn() });
    runPreparedMock.mockImplementation(async (p: { runDispatch: () => Promise<void> }) => {
      await p.runDispatch();
    });

    const dispatchReplyMock = vi.fn().mockResolvedValue(undefined);
    const runtime = {
      channel: {
        reply: {
          finalizeInboundContext: vi.fn((c: unknown) => ({ ctx: c })),
          dispatchReplyWithBufferedBlockDispatcher: dispatchReplyMock,
        },
        session: { recordInboundSession: vi.fn(), resolveStorePath: vi.fn(() => "/store") },
      },
    };

    const channelDeliver = vi.fn().mockResolvedValue(undefined);

    await dispatchInboundDirectDmWithReasoning({
      cfg: { session: { store: "store" } },
      runtime,
      channel: "sellerclaw-ui",
      channelLabel: "SellerClaw UI",
      accountId: "default",
      peer: { kind: "direct", id: "c1" },
      senderId: "u1",
      senderAddress: "sellerclaw-ui:u1",
      recipientAddress: "sellerclaw-ui:direct:c1",
      conversationLabel: "c1",
      rawBody: "hi",
      messageId: "m1",
      timestamp: 123,
      commandAuthorized: true,
      deliver: channelDeliver,
    });

    // The engine invokes the dispatcher's deliver as ``deliver(payload, info)``; the wrapper
    // must relay ``info`` (the block/final label) to the channel's deliver, not drop it.
    const innerDeliver = (
      dispatchReplyMock.mock.calls[0]![0] as {
        dispatcherOptions: { deliver: (p: unknown, info?: unknown) => Promise<void> };
      }
    ).dispatcherOptions.deliver;
    const info = { kind: "final", assistantMessageIndex: 2 };
    await innerDeliver({ text: "done" }, info);

    expect(channelDeliver).toHaveBeenCalledWith({ text: "done" }, info);

    // The engine's error flag survives normalization (plugin-sdk's normalizer strips it, so
    // the wrapper re-attaches it) — and only exactly-true counts as the signal.
    await innerDeliver({ text: "LLM request timed out.", isError: true }, { kind: "final" });
    expect(channelDeliver).toHaveBeenLastCalledWith(
      { text: "LLM request timed out.", isError: true },
      { kind: "final" },
    );
    await innerDeliver({ text: "quoting isError: true here", isError: "true" }, { kind: "final" });
    const lastPayload = channelDeliver.mock.calls.at(-1)![0] as Record<string, unknown>;
    expect("isError" in lastPayload && lastPayload.isError === true).toBe(false);
  });
});
