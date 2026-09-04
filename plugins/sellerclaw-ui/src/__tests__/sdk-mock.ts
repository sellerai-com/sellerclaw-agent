import { vi } from "vitest";

vi.mock("openclaw/plugin-sdk/core", () => ({
  createChannelPluginBase: (cfg: unknown) => cfg,
  createChatChannelPlugin: (cfg: unknown) => cfg,
  // Faithful-enough reproduction of the SDK's route builder for the dm scopes the bundle uses:
  // the real one delegates key construction to core's ``buildAgentPeerSessionKey``.
  buildChannelOutboundSessionRoute: (params: {
    cfg: { session?: { dmScope?: string; mainKey?: string } };
    agentId: string;
    channel: string;
    recipientSessionExact?: boolean | string;
    peer: { kind: string; id: string };
    chatType: string;
    from: string;
    to: string;
  }) => {
    const dmScope = params.cfg.session?.dmScope ?? "main";
    const peerId = params.peer.id.toLowerCase();
    const sessionKey =
      dmScope === "per-channel-peer" && peerId
        ? `agent:${params.agentId}:${params.channel}:direct:${peerId}`
        : `agent:${params.agentId}:${params.cfg.session?.mainKey ?? "main"}`;
    return {
      sessionKey,
      baseSessionKey: sessionKey,
      ...(params.recipientSessionExact !== undefined
        ? { recipientSessionExact: params.recipientSessionExact }
        : {}),
      peer: params.peer,
      chatType: params.chatType,
      from: params.from,
      to: params.to,
    };
  },
}));

/**
 * Slots keyed the way the real SDK keys them, so a store opened twice for the same plugin id is
 * the same slot — that sharing is the whole reason ``runtime-store.ts`` passes ``pluginId``.
 */
const mockRuntimeSlots = new Map<string, { runtime: unknown }>();

vi.mock("openclaw/plugin-sdk/runtime-store", () => ({
  createPluginRuntimeStore: (options: string | { pluginId: string; errorMessage: string }) => {
    const key = typeof options === "string" ? options : `plugin-runtime:${options.pluginId}`;
    let slot = mockRuntimeSlots.get(key);
    if (!slot) {
      slot = { runtime: undefined };
      mockRuntimeSlots.set(key, slot);
    }
    const held = slot;
    return {
      setRuntime(v: unknown) {
        held.runtime = v;
      },
      clearRuntime() {
        held.runtime = undefined;
      },
      tryGetRuntime() {
        return held.runtime ?? null;
      },
      getRuntime() {
        if (held.runtime === undefined) {
          throw new Error("PluginRuntime not set");
        }
        return held.runtime;
      },
    };
  },
}));

// The gateway request scope our route handlers repair before starting a turn
// (``gateway-scope.ts``). Unmocked, every test file that reaches ``inbound.ts`` fails to resolve
// it. The default is "no scope at all", which is what a unit test's plain function call looks
// like to that module; tests that care override it.
vi.mock("openclaw/plugin-sdk/plugin-runtime", () => ({
  getPluginRuntimeGatewayRequestScope: vi.fn(() => undefined),
}));

vi.mock("openclaw/plugin-sdk/channel-inbound", () => ({
  dispatchInboundDirectDmWithRuntime: vi.fn().mockResolvedValue(undefined),
  runPreparedInboundReply: vi.fn().mockResolvedValue(undefined),
}));

// Building blocks our local ``inbound-reply-with-reasoning`` composes. The plugin imports that
// module at load time, so every test file that loads the plugin needs these specifiers to resolve
// (they only exist inside the OpenClaw container at runtime). Tests that actually exercise the
// dispatch mock the local module or these directly; here we just keep imports resolvable.
vi.mock("openclaw/plugin-sdk/inbound-envelope", () => ({
  resolveInboundRouteEnvelopeBuilderWithRuntime: vi.fn(() => ({
    route: { sessionKey: "test-session", agentId: "supervisor", accountId: "default" },
    buildEnvelope: () => ({ storePath: "/tmp/store", body: "envelope" }),
  })),
}));

vi.mock("openclaw/plugin-sdk/channel-reply-pipeline", () => ({
  createChannelReplyPipeline: vi.fn(() => ({ onModelSelected: vi.fn() })),
}));

vi.mock("openclaw/plugin-sdk/reply-payload", () => ({
  // Lightweight reproduction of the SDK's check: a payload is reasoning when
  // ``isReasoning === true`` or the text starts with ``reasoning:`` / ``thinking…_``.
  isReasoningReplyPayload: (payload: Record<string, unknown>) => {
    if (payload?.isReasoning === true) return true;
    const text = typeof payload?.text === "string" ? payload.text : "";
    return /^(?:reasoning:|thinking\.{0,3}(?=\s*(?:>\s*)?_))/iu.test(text.trimStart());
  },
  normalizeOutboundReplyPayload: (payload: unknown) =>
    payload && typeof payload === "object" ? (payload as Record<string, unknown>) : {},
}));

vi.mock("openclaw/plugin-sdk/agent-harness-runtime", () => ({
  abortAgentHarnessRun: vi.fn(),
  resolveActiveEmbeddedRunSessionId: vi.fn().mockReturnValue(null),
}));

vi.mock("openclaw/plugin-sdk/webhook-ingress", () => ({
  readJsonWebhookBodyOrReject: vi.fn(),
}));

vi.mock("openclaw/plugin-sdk/media-store", () => ({
  saveMediaBuffer: vi.fn().mockResolvedValue({
    id: "media-id",
    path: "/home/node/.openclaw/media/inbound/media-id",
    size: 0,
    contentType: "application/octet-stream",
  }),
  resolveMediaBufferPath: vi
    .fn()
    .mockResolvedValue("/home/node/.openclaw/media/inbound/media-id"),
}));
