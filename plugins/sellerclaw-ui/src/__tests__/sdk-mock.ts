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

// The engine's silent-reply contract (``src/auto-reply/tokens.ts``), reproduced closely enough for
// the shapes our code relies on: a token-only reply, case-insensitive, tolerant of repeats and of
// punctuation at the edges, plus the JSON-wrapped and reasoning-prefixed forms the payload-aware
// variant adds. The real module lives inside the OpenClaw container.
vi.mock("openclaw/plugin-sdk/reply-chunking", () => {
  const TOKEN = "NO_REPLY";
  const exactRe = new RegExp(`^\\s*${TOKEN}(?:\\s+${TOKEN})*\\s*$`, "i");
  const stripEdgePunctuation = (text: string) =>
    text.replace(/^\p{P}+/u, "").replace(/\p{P}+$/u, "");
  const isSilentReplyText = (text: string) =>
    Boolean(text) && (exactRe.test(text) || exactRe.test(stripEdgePunctuation(text.trim())));
  const isJsonString = (text: string) => {
    const trimmed = text.trim();
    if (!trimmed.startsWith('"') || !trimmed.endsWith('"') || !trimmed.includes(TOKEN)) return false;
    try {
      const parsed: unknown = JSON.parse(trimmed);
      return typeof parsed === "string" && parsed.trim() === TOKEN;
    } catch {
      return false;
    }
  };
  const isJsonEnvelope = (text: string) => {
    const trimmed = text.trim();
    if (!trimmed.startsWith("{") || !trimmed.endsWith("}") || !trimmed.includes(TOKEN)) return false;
    try {
      const parsed: unknown = JSON.parse(trimmed);
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return false;
      const keys = Object.keys(parsed);
      return (
        keys.length === 1 &&
        keys[0] === "action" &&
        (parsed as { action?: unknown }).action === TOKEN
      );
    } catch {
      return false;
    }
  };
  const reasoningPrefixRe =
    /^\s*(?:<\s*(?:think(?:ing)?|thought)\b[^<>]*>[\s\S]*?<\s*\/\s*(?:think(?:ing)?|thought)\s*>|(?:think(?:ing)?|thought|analysis|reasoning)\s*:?\s*\r?\n)/i;
  const silentIntentRe =
    /^(?:i|we)(?:'ll| will)? ?(?:have nothing (?:to|for) (?:say|add)|stay (?:quiet|silent)|(?:do not|don't|won't) reply)\.?$/i;
  const isReasoningPrefixedSilent = (text: string) => {
    const trimmed = text.trim();
    if (!reasoningPrefixRe.test(trimmed)) return false;
    const rest = trimmed.replace(reasoningPrefixRe, "").trim();
    if (isSilentReplyText(rest)) return true;
    const withoutToken = rest.replace(new RegExp(`(?:^|[\\s*.])${TOKEN}\\s*$`, "i"), "").trim();
    return withoutToken !== rest && (!withoutToken || silentIntentRe.test(withoutToken));
  };
  return {
    SILENT_REPLY_TOKEN: TOKEN,
    isSilentReplyText,
    isSilentReplyPayloadText: (text: string) =>
      isSilentReplyText(text) ||
      isJsonString(text) ||
      isJsonEnvelope(text) ||
      isReasoningPrefixedSilent(text),
  };
});

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
