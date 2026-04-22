/**
 * Ambient typings for OpenClaw plugin SDK (resolved inside the gateway container).
 */

declare module "openclaw/plugin-sdk/config-runtime" {
  export type OpenClawConfig = Record<string, unknown>;
}

declare module "openclaw/plugin-sdk/secret-input" {
  export function normalizeResolvedSecretInputString(opts: {
    value: unknown;
    path: string;
  }): unknown;
  export function normalizeSecretInput(value: unknown): string | undefined;
}

declare module "openclaw/plugin-sdk/text-runtime" {
  export function normalizeOptionalString(value: unknown): string | undefined;
}

declare module "openclaw/plugin-sdk/security-runtime" {
  export function wrapWebContent(text: string, source: string): string;
}

declare module "openclaw/plugin-sdk/provider-web-search-contract" {
  type OpenClawConfigLocal = import("openclaw/plugin-sdk/config-runtime").OpenClawConfig;
  export type WebSearchTool = {
    description?: string;
    parameters?: unknown;
    /** OpenClaw 2026.4.x passes a single params object (same as bundled Tavily provider). */
    execute: (args: Record<string, unknown>) => Promise<unknown>;
  };
  export type WebSearchProviderPlugin = {
    id: string;
    label: string;
    hint?: string;
    credentialPath?: string;
    createTool?: (ctx: { config?: OpenClawConfigLocal }) => WebSearchTool;
  } & Record<string, unknown>;
  export function createWebSearchProviderContractFields(
    options: Record<string, unknown>,
  ): Record<string, unknown>;
}

declare module "openclaw/plugin-sdk/plugin-entry" {
  export function definePluginEntry(opts: {
    id: string;
    name: string;
    description: string;
    register(api: { registerWebSearchProvider: (p: unknown) => void }): void;
  }): unknown;
}
