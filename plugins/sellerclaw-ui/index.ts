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
 * The BUNDLED channel contract, not the generic plugin one.
 *
 * This plugin ships inside the image under `/app/dist/extensions/`, so OpenClaw finds it with its
 * own built-in channel discovery. That path insists on this contract (`kind:
 * "bundled-channel-entry"`); an entry without it is skipped with a warning on every gateway start
 * and the plugin falls back to the legacy plugin loader. The two paths are mutually exclusive —
 * the legacy loader rejects this contract outright — so a change of directory is a change of
 * contract.
 *
 * What it buys beyond a quiet log: the channel and its runtime setter are named as references and
 * loaded only when a pass actually registers the channel, so the metadata-only passes (and there
 * is one around every embedded agent run) stop pulling the whole channel in.
 *
 * The flip side of that laziness: a referenced sidecar can be evaluated a second time,
 * independently of this file's own static imports. Nothing shared may sit in a module-level
 * variable — see `shared-state.ts`, and the slots in `channel.ts` / `send.ts` / `runtime-store.ts`
 * that exist for exactly this reason.
 *
 * Lifecycle hooks are NOT registered here: `registerFull` runs only in the "full" pass, and
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
