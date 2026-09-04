import { defineBundledChannelEntry } from "openclaw/plugin-sdk/channel-entry-contract";
import type { OpenClawPluginApi } from "openclaw/plugin-sdk/core";

import { setPluginConfig } from "./src/channel.js";
import { withPerPassHooks } from "./src/hook-registration.js";
import {
  registerAbortRoute,
  registerFeasibilityCheckRoute,
  registerInboundRoute,
  registerScheduledRunRoute,
} from "./src/inbound.js";

/**
 * The bundled channel entry for SellerClaw UI.
 *
 * `defineBundledChannelEntry` — not the plain `defineChannelPluginEntry` — because this plugin
 * ships inside the image (`/app/dist/extensions/`), and OpenClaw discovers anything there with
 * its native bundled-channel scan. That scan demands this exact contract: an entry without it is
 * logged as `missing bundled-channel-entry contract; skipping` and falls back to the legacy
 * plugin loader. The fallback works, but it warns on every gateway start and every registry pass.
 *
 * The contract takes module REFERENCES rather than values, and that is the point: OpenClaw
 * imports `channel.ts` only when it actually needs the channel, so metadata-only registry passes
 * stop pulling the whole channel graph in. It also means those pieces arrive through a loader
 * boundary of their own — see the process-wide slots in `shared-state.ts`, `send.ts` and
 * `runtime-store.ts`, which is what keeps a second evaluation from splitting our state in two.
 *
 * Lifecycle hooks (completion-delivery guard, run-outcome tracker, reasoning relay, live
 * thinking stream) are NOT registered in `registerFull`: it runs only in the "full" pass, and
 * OpenClaw replaces the hook registry on every re-activation — see `withPerPassHooks`.
 */
export default withPerPassHooks(
  defineBundledChannelEntry({
    id: "sellerclaw-ui",
    name: "SellerClaw UI",
    description: "Delivers assistant messages to the SellerClaw web chat.",
    importMetaUrl: import.meta.url,
    plugin: { specifier: "./src/channel.js", exportName: "sellerclawUiChannelPlugin" },
    runtime: { specifier: "./src/runtime-store.js", exportName: "setRuntime" },
    registerFull(api: OpenClawPluginApi) {
      setPluginConfig(api.config);
      registerInboundRoute(api);
      registerAbortRoute(api);
      registerScheduledRunRoute(api);
      registerFeasibilityCheckRoute(api);
    },
  }),
);
