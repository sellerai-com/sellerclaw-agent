import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";
import { createSellerclawWebSearchProvider } from "./src/provider.js";

export default definePluginEntry({
  id: "sellerclaw-web-search",
  name: "SellerClaw Web Search",
  description: "Routes web_search through the SellerClaw API.",
  register(api) {
    api.registerWebSearchProvider(createSellerclawWebSearchProvider());
  },
});
