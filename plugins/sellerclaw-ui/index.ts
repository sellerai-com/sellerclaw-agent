import { defineChannelPluginEntry } from "openclaw/plugin-sdk/core";
import type { OpenClawPluginApi } from "openclaw/plugin-sdk/core";

import { sellerclawUiChannelPlugin, setPluginConfig } from "./src/channel.js";
import { registerCompletionDeliveryGuard } from "./src/completion-delivery.js";
import {
  registerAbortRoute,
  registerFeasibilityCheckRoute,
  registerInboundRoute,
  registerScheduledRunRoute,
} from "./src/inbound.js";
import { setRuntime } from "./src/runtime-store.js";

export default defineChannelPluginEntry({
  id: "sellerclaw-ui",
  name: "SellerClaw UI",
  description: "Delivers assistant messages to the SellerClaw web chat.",
  plugin: sellerclawUiChannelPlugin,
  setRuntime,
  registerFull(api: OpenClawPluginApi) {
    setPluginConfig(api.config);
    registerInboundRoute(api);
    registerAbortRoute(api);
    registerScheduledRunRoute(api);
    registerFeasibilityCheckRoute(api);
    registerCompletionDeliveryGuard(api);
  },
});
