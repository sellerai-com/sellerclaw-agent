import type { OpenClawConfig } from "openclaw/plugin-sdk/config-runtime";
import {
  normalizeResolvedSecretInputString,
  normalizeSecretInput,
} from "openclaw/plugin-sdk/secret-input";
import { normalizeOptionalString } from "openclaw/plugin-sdk/text-runtime";

export const SELLERCLAW_WEB_SEARCH_PLUGIN_ID = "sellerclaw-web-search" as const;

const AUTH_PATH = "plugins.entries.sellerclaw-web-search.config.webSearch.authToken";

type WebSearchSection = {
  authToken?: unknown;
  baseUrl?: string;
};

type PluginEntry = {
  config?: {
    webSearch?: WebSearchSection;
  };
};

function readWebSearchSection(cfg?: OpenClawConfig): WebSearchSection | undefined {
  const plugins = cfg?.plugins as { entries?: Record<string, PluginEntry> } | undefined;
  const ws = plugins?.entries?.[SELLERCLAW_WEB_SEARCH_PLUGIN_ID]?.config?.webSearch;
  if (ws && typeof ws === "object" && !Array.isArray(ws)) {
    return ws;
  }
  return undefined;
}

function normalizeConfiguredSecret(value: unknown, path: string): string | undefined {
  return normalizeSecretInput(
    normalizeResolvedSecretInputString({
      value,
      path,
    }),
  );
}

export function resolveSellerclawAuthToken(cfg?: OpenClawConfig): string | undefined {
  const search = readWebSearchSection(cfg);
  return normalizeConfiguredSecret(search?.authToken, AUTH_PATH) ?? undefined;
}

export function resolveSellerclawBaseUrl(cfg?: OpenClawConfig): string {
  const search = readWebSearchSection(cfg);
  const configured = (normalizeOptionalString(search?.baseUrl) ?? "").trim();
  return configured.replace(/\/$/, "");
}
