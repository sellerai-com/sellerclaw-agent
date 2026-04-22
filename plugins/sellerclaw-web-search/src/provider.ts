import type { OpenClawConfig } from "openclaw/plugin-sdk/config-runtime";
import {
  createWebSearchProviderContractFields,
  type WebSearchProviderPlugin,
} from "openclaw/plugin-sdk/provider-web-search-contract";
import { SELLERCLAW_WEB_SEARCH_PLUGIN_ID } from "./config.js";
import { runSellerclawWebSearch } from "./client.js";

const SELLERCLAW_CREDENTIAL_PATH =
  "plugins.entries.sellerclaw-web-search.config.webSearch.authToken";

const GenericSellerclawSearchSchema = {
  type: "object",
  properties: {
    query: { type: "string", description: "Search query string." },
    count: {
      type: "number",
      description: "Number of results to return (1-20).",
      minimum: 1,
      maximum: 20,
    },
  },
  additionalProperties: false,
} satisfies Record<string, unknown>;

export function createSellerclawWebSearchProvider(): WebSearchProviderPlugin {
  return {
    id: SELLERCLAW_WEB_SEARCH_PLUGIN_ID,
    label: "SellerClaw Search",
    hint: "Search via SellerClaw backend (billed; no web-search API keys on the agent).",
    credentialPath: SELLERCLAW_CREDENTIAL_PATH,
    ...createWebSearchProviderContractFields({
      credentialPath: SELLERCLAW_CREDENTIAL_PATH,
      searchCredential: { type: "none" },
      configuredCredential: { pluginId: SELLERCLAW_WEB_SEARCH_PLUGIN_ID, field: "authToken" },
      selectionPluginId: SELLERCLAW_WEB_SEARCH_PLUGIN_ID,
    }),
    // Match upstream Tavily web-search provider (OpenClaw 2026.4.x): tool is `{ description, parameters, execute(args) }`.
    createTool: (ctx: { config?: OpenClawConfig }) => ({
      description:
        "Search the web via the SellerClaw API. Uses the configured agent bearer token; upstream provider is chosen on the server.",
      parameters: GenericSellerclawSearchSchema,
      execute: async (args: Record<string, unknown>) => {
        // Static import: OpenClaw’s loader can resolve dynamic `import("./client.js")` to a chunk
        // where `./config.js` bindings are undefined (runtime error on resolveSellerclawAuthToken).
        return await runSellerclawWebSearch({
          cfg: ctx.config,
          query: typeof args.query === "string" ? args.query : "",
          maxResults: typeof args.count === "number" ? args.count : undefined,
        });
      },
    }),
  };
}
