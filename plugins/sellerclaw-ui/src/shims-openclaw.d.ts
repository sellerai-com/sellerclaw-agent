/**
 * Minimal typings for OpenClaw plugin SDK modules. At runtime these resolve inside the OpenClaw container.
 */
declare module "openclaw/plugin-sdk/runtime-store" {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  export type PluginRuntime = any;
  /**
   * ``pluginId`` matters: that form parks the slot on ``globalThis`` (keyed by the id), so every
   * evaluation of every module sees the same runtime. The legacy string form keeps the slot in
   * the calling module instance, which loses the runtime as soon as the module is evaluated
   * twice — and OpenClaw evaluates plugin modules on every registry pass.
   */
  export function createPluginRuntimeStore(
    options: string | { pluginId: string; errorMessage: string },
  ): {
    setRuntime(runtime: PluginRuntime): void;
    clearRuntime(): void;
    tryGetRuntime(): PluginRuntime | null;
    getRuntime(): PluginRuntime;
  };
}

declare module "openclaw/plugin-sdk/plugin-runtime" {
  export function getPluginRuntimeGatewayRequestScope():
    | {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        context?: { resolveGatewayContext?: () => any } & Record<string, any>;
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        resolveGatewayContext?: () => any;
      }
    | undefined;
}

declare module "openclaw/plugin-sdk/channel-inbound" {
  export function dispatchInboundDirectDmWithRuntime(
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    params: Record<string, any>,
  ): Promise<void>;
  // Inner step of the direct-DM dispatch (session recording + runDispatch); used by our local
  // re-implementation that forwards reasoning-stream callbacks. See inbound-reply-with-reasoning.ts.
  export function runPreparedInboundReply(
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    params: Record<string, any>,
  ): Promise<void>;
}

declare module "openclaw/plugin-sdk/inbound-envelope" {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  export function resolveInboundRouteEnvelopeBuilderWithRuntime(params: Record<string, any>): {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    route: Record<string, any>;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    buildEnvelope: (args: Record<string, any>) => { storePath: unknown; body: unknown };
  };
}

declare module "openclaw/plugin-sdk/channel-reply-pipeline" {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  export function createChannelReplyPipeline(params: Record<string, any>): {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    onModelSelected: any;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    [key: string]: any;
  };
}

declare module "openclaw/plugin-sdk/agent-harness-runtime" {
  /** Abort the in-flight embedded reply run for a resolved session id. */
  export function abortAgentHarnessRun(sessionId: string, opts?: unknown): void;
  /** Resolve the active embedded run's session id for a session key, or null if none. */
  export function resolveActiveEmbeddedRunSessionId(sessionKey: string): string | null;
}

declare module "openclaw/plugin-sdk/media-store" {
  export function saveMediaBuffer(
    buffer: Buffer,
    contentType?: string,
    subdir?: string,
    maxBytes?: number,
    originalFilename?: string,
  ): Promise<{ id: string; path: string; size: number; contentType: string }>;
  export function resolveMediaBufferPath(id: string, subdir?: string): Promise<string>;
}

declare module "openclaw/plugin-sdk/webhook-ingress" {
  import type { IncomingMessage, ServerResponse } from "node:http";
  export function readJsonWebhookBodyOrReject(opts: {
    req: IncomingMessage;
    res: ServerResponse;
  }): Promise<
    | { ok: true; value: unknown }
    | { ok: false }
    | undefined
    | false
  >;
}

declare module "openclaw/plugin-sdk/core" {
  import type { IncomingMessage, ServerResponse } from "node:http";

  export type OpenClawConfig = Record<string, unknown>;

  export type OpenClawPluginApi = {
    config: OpenClawConfig;
    logger: {
      info?: (msg: string) => void;
      warn?: (msg: string) => void;
      error?: (msg: string) => void;
    };
    registerHttpRoute: (opts: {
      path: string;
      auth: string;
      handler: (
        req: IncomingMessage,
        res: ServerResponse,
      ) => boolean | Promise<boolean>;
    }) => void;
    /**
     * Typed lifecycle hook registration. Present in the "full", "discovery" and
     * "tool-discovery" registration modes and absent in metadata-only passes, hence optional —
     * callers must feature-check before using it. Note the registry it lands in is REPLACED on
     * every activation pass (see `hook-registration.ts`), so registering once is not enough.
     */
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    on?: (hookName: string, handler: (event: any, ctx?: any) => any, opts?: unknown) => void;
    /**
     * Sequenced agent events, for the runs no callback of ours can reach.
     *
     * Unlike a hook, a subscription sees EVERY run in the gateway process — announce/settle runs
     * and subagents included — and sees it while it happens. `sessionKey` is stamped on
     * `lifecycle` events unconditionally but stripped from the other streams for channels the
     * Control UI does not own (ours is one), so a subscriber that needs an address builds it from
     * the lifecycle events. Optional: absent in metadata-only registration passes.
     */
    registerAgentEventSubscription?: AgentEventSubscriptionRegistrar;
    agent?: {
      events?: {
        registerAgentEventSubscription?: AgentEventSubscriptionRegistrar;
      };
    };
  };

  /** Registers one agent-event subscription. */
  export type AgentEventSubscriptionRegistrar = (subscription: {
    id: string;
    description?: string;
    streams?: string[];
    handle: (event: AgentEventPayload) => void | Promise<void>;
  }) => void;

  /** One sequenced agent event as a subscriber receives it. */
  export type AgentEventPayload = {
    runId: string;
    seq: number;
    stream: string;
    ts: number;
    data: Record<string, unknown>;
    sessionKey?: string;
    sessionId?: string;
    agentId?: string;
  };

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  export function defineChannelPluginEntry(opts: Record<string, any>): {
    register: (api: OpenClawPluginApi) => void;
  };
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  export function defineSetupPluginEntry(opts: Record<string, any>): unknown;
  /** Runtime returns a rich plugin object; locally we use `any` so tests can narrow. */
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  export function createChannelPluginBase(cfg: unknown): any;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  export function createChatChannelPlugin<T = unknown>(cfg: unknown): any;

  /** Session route a channel's outbound target maps to, used to mirror sends into transcripts. */
  export type ChannelOutboundSessionRoute = {
    sessionKey: string;
    baseSessionKey: string;
    recipientSessionExact?: boolean | "direct-alias" | "delivery-identity";
    peer: { kind: "direct" | "group" | "channel"; id: string };
    chatType: "direct" | "group" | "channel";
    from: string;
    to: string;
    threadId?: string | number;
  };
  export function buildChannelOutboundSessionRoute(params: {
    cfg: OpenClawConfig;
    agentId: string;
    channel: string;
    accountId?: string | null;
    recipientSessionExact?: boolean | "direct-alias" | "delivery-identity";
    peer: { kind: "direct" | "group" | "channel"; id: string };
    chatType: "direct" | "group" | "channel";
    from: string;
    to: string;
    threadId?: string | number;
  }): ChannelOutboundSessionRoute;
}

declare module "openclaw/plugin-sdk/channel-entry-contract" {
  import type { OpenClawPluginApi } from "openclaw/plugin-sdk/core";

  /**
   * A sidecar module this entry owns, named the way OpenClaw resolves it: the specifier is
   * relative to the entry file and written with a ``.js`` extension even though we ship
   * TypeScript (the loader tries ``.js`` then the matching ``.ts``).
   */
  export type BundledEntryModuleRef = { specifier: string; exportName?: string };

  /**
   * Declares the entry as a BUNDLED channel, i.e. one OpenClaw discovers itself under
   * ``<packageRoot>/dist/extensions`` rather than loading from a configured path.
   *
   * The sidecars named here are loaded through the entry boundary, on demand — which is the
   * point: metadata-only registry passes stop pulling the whole channel in. It also means a
   * sidecar can be evaluated a SECOND time, separately from this entry's own static imports, so
   * nothing shared may live in a module-level variable (see ``shared-state.ts``).
   */
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  export function defineBundledChannelEntry(opts: {
    id: string;
    name: string;
    description: string;
    /** Always ``import.meta.url`` — every sidecar specifier is resolved against it. */
    importMetaUrl: string;
    plugin: BundledEntryModuleRef;
    outbound?: BundledEntryModuleRef;
    secrets?: BundledEntryModuleRef;
    /** Export taking the plugin runtime; called on every pass that registers the channel. */
    runtime?: BundledEntryModuleRef;
    accountInspect?: BundledEntryModuleRef;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    configSchema?: any;
    features?: { accountInspect?: boolean };
    registerCliMetadata?: (api: OpenClawPluginApi) => void;
    registerFull?: (api: OpenClawPluginApi) => void;
    registerCapabilities?: (api: OpenClawPluginApi) => void;
  }): {
    kind: "bundled-channel-entry";
    id: string;
    name: string;
    description: string;
    register: (api: OpenClawPluginApi) => void;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    loadChannelPlugin: (options?: unknown) => any;
  };

  /** The onboarding/migration half of the same contract. */
  export function defineBundledChannelSetupEntry(opts: {
    importMetaUrl: string;
    plugin: BundledEntryModuleRef;
    secrets?: BundledEntryModuleRef;
    runtime?: BundledEntryModuleRef;
    registerSetupRuntime?: (api: OpenClawPluginApi) => void;
    features?: { legacySessionSurfaces?: boolean };
  }): {
    kind: "bundled-channel-setup-entry";
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    loadSetupPlugin: (options?: unknown) => any;
  };
}

declare module "openclaw/plugin-sdk/channel-entry-contract" {
  import type { OpenClawPluginApi } from "openclaw/plugin-sdk/core";

  /**
   * Where a bundled entry finds one of its own modules: a specifier resolved against the
   * entry's ``importMetaUrl``, plus which export to take from it. The point of the
   * indirection is laziness — OpenClaw imports the referenced module only when it actually
   * needs that piece, so metadata-only registry passes no longer pull the whole channel in.
   */
  export type BundledEntryModuleRef = {
    specifier: string;
    exportName?: string;
  };

  export type BundledChannelEntryContract = {
    kind: "bundled-channel-entry";
    id: string;
    name: string;
    description: string;
    register: (api: OpenClawPluginApi) => void;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    loadChannelPlugin: (options?: unknown) => any;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    [key: string]: any;
  };

  export type BundledChannelSetupEntryContract = {
    kind: "bundled-channel-setup-entry";
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    loadSetupPlugin: (options?: unknown) => any;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    [key: string]: any;
  };

  export function defineBundledChannelEntry(opts: {
    id: string;
    name: string;
    description: string;
    importMetaUrl: string;
    plugin: BundledEntryModuleRef;
    outbound?: BundledEntryModuleRef;
    secrets?: BundledEntryModuleRef;
    runtime?: BundledEntryModuleRef;
    accountInspect?: BundledEntryModuleRef;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    configSchema?: any;
    features?: { accountInspect?: boolean };
    registerCliMetadata?: (api: OpenClawPluginApi) => void;
    registerFull?: (api: OpenClawPluginApi) => void;
    registerCapabilities?: (api: OpenClawPluginApi) => void;
  }): BundledChannelEntryContract;

  export function defineBundledChannelSetupEntry(opts: {
    importMetaUrl: string;
    plugin: BundledEntryModuleRef;
    secrets?: BundledEntryModuleRef;
    runtime?: BundledEntryModuleRef;
    registerSetupRuntime?: (api: OpenClawPluginApi) => void;
    features?: { legacySessionSurfaces?: boolean };
  }): BundledChannelSetupEntryContract;
}

declare module "openclaw/plugin-sdk/reply-payload" {
  /** Classify a deliver payload as the runtime's reasoning ("thinking") channel. */
  export function isReasoningReplyPayload(payload: Record<string, unknown>): boolean;
  /** Normalize an outbound reply payload to the fields channels receive. */
  export function normalizeOutboundReplyPayload(payload: unknown): Record<string, unknown>;
}
