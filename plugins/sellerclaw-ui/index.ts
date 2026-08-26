import { defineChannelPluginEntry } from "openclaw/plugin-sdk/core";
import type { OpenClawPluginApi } from "openclaw/plugin-sdk/core";

import { sellerclawUiChannelPlugin, setPluginConfig } from "./src/channel.js";
import { withPerPassHooks } from "./src/hook-registration.js";
import {
  registerAbortRoute,
  registerFeasibilityCheckRoute,
  registerInboundRoute,
  registerScheduledRunRoute,
} from "./src/inbound.js";
import { setRuntime } from "./src/runtime-store.js";

// Lifecycle hooks (completion-delivery guard, run-outcome tracker) are NOT registered here:
// `registerFull` runs only in the "full" registry pass, and OpenClaw 2026.8 replaces the hook
// registry on every re-activation — see `withPerPassHooks`, which registers them on every pass.
export default withPerPassHooks(
  defineChannelPluginEntry({
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
    },
  }),
);
