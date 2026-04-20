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
