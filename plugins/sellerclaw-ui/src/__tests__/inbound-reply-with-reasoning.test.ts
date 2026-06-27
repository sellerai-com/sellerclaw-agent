import { describe, expect, it, vi } from "vitest";

const { resolveRouteMock, createPipelineMock, runPreparedMock, normalizeMock } = vi.hoisted(() => ({
  resolveRouteMock: vi.fn(),
  createPipelineMock: vi.fn(),
  runPreparedMock: vi.fn(),
  normalizeMock: vi.fn((p: unknown) => p),
}));

vi.mock("openclaw/plugin-sdk/inbound-envelope", () => ({
  resolveInboundRouteEnvelopeBuilderWithRuntime: (...a: unknown[]) => resolveRouteMock(...a),
}));
vi.mock("openclaw/plugin-sdk/channel-reply-pipeline", () => ({
  createChannelReplyPipeline: (...a: unknown[]) => createPipelineMock(...a),
}));
vi.mock("openclaw/plugin-sdk/channel-inbound", () => ({
  runPreparedInboundReply: (...a: unknown[]) => runPreparedMock(...a),
}));
vi.mock("openclaw/plugin-sdk/reply-payload", () => ({
  normalizeOutboundReplyPayload: (p: unknown) => normalizeMock(p),
}));

import { dispatchInboundDirectDmWithReasoning } from "../inbound-reply-with-reasoning.js";

describe("dispatchInboundDirectDmWithReasoning", () => {
  it("forwards onReasoningStream/onReasoningEnd into the reply dispatcher's replyOptions", async () => {
    resolveRouteMock.mockReturnValue({
      route: { sessionKey: "sk", agentId: "supervisor", accountId: "default" },
      buildEnvelope: () => ({ storePath: "/store", body: "envelope-body" }),
    });
    const onModelSelected = vi.fn();
    createPipelineMock.mockReturnValue({ onModelSelected, pipelineField: "x" });

    // runPreparedInboundReply wraps the actual dispatch; run it so we capture the inner call.
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
        session: { recordInboundSession: vi.fn() },
      },
    };

    const onReasoningStream = vi.fn();
    const onReasoningEnd = vi.fn();

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
      replyOptions: { onReasoningStream, onReasoningEnd },
      deliver: vi.fn().mockResolvedValue(undefined),
    });

    expect(dispatchReplyMock).toHaveBeenCalledTimes(1);
    const callArg = dispatchReplyMock.mock.calls[0]![0] as {
      replyOptions: { onModelSelected: unknown; onReasoningStream: unknown; onReasoningEnd: unknown };
    };
    // The whole point of the local re-implementation: reasoning callbacks survive into replyOptions
    // (upstream dropped them, passing only onModelSelected).
    expect(callArg.replyOptions.onReasoningStream).toBe(onReasoningStream);
    expect(callArg.replyOptions.onReasoningEnd).toBe(onReasoningEnd);
    expect(callArg.replyOptions.onModelSelected).toBe(onModelSelected);
  });

  it("forwards the dispatcher's per-delivery info (kind/index) as the second deliver arg", async () => {
    resolveRouteMock.mockReturnValue({
      route: { sessionKey: "sk", agentId: "supervisor", accountId: "default" },
      buildEnvelope: () => ({ storePath: "/store", body: "envelope-body" }),
    });
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
        session: { recordInboundSession: vi.fn() },
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
  });
});
