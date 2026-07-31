/**
 * Minimal typings for OpenClaw plugin SDK modules. At runtime these resolve inside the OpenClaw container.
 */
declare module "openclaw/plugin-sdk/runtime-store" {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  export type PluginRuntime = any;
  export function createPluginRuntimeStore(errorMessage: string): {
    setRuntime(runtime: PluginRuntime): void;
    clearRuntime(): void;
    tryGetRuntime(): PluginRuntime | null;
    getRuntime(): PluginRuntime;
  };
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
     * Typed lifecycle hook registration. Present only when the plugin is loaded in "full"
     * registration mode, hence optional — callers must feature-check before using it.
     */
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    on?: (hookName: string, handler: (event: any, ctx?: any) => any, opts?: unknown) => void;
  };

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  export function defineChannelPluginEntry(opts: Record<string, any>): unknown;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  export function defineSetupPluginEntry(opts: Record<string, any>): unknown;
  /** Runtime returns a rich plugin object; locally we use `any` so tests can narrow. */
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  export function createChannelPluginBase(cfg: unknown): any;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  export function createChatChannelPlugin<T = unknown>(cfg: unknown): any;
}

declare module "openclaw/plugin-sdk/reply-payload" {
  /** Classify a deliver payload as the runtime's reasoning ("thinking") channel. */
  export function isReasoningReplyPayload(payload: Record<string, unknown>): boolean;
  /** Normalize an outbound reply payload to the fields channels receive. */
  export function normalizeOutboundReplyPayload(payload: unknown): Record<string, unknown>;
}
