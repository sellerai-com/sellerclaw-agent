import { defineBundledChannelSetupEntry } from "openclaw/plugin-sdk/channel-entry-contract";

/**
 * The onboarding half of the bundled channel contract (`kind: "bundled-channel-setup-entry"`).
 *
 * Same rule as `index.ts`: built-in channel discovery only accepts this shape, and skips a
 * legacy `defineSetupPluginEntry` with a warning. The channel is named as a reference so setup
 * surfaces can read its config schema without loading the delivery path.
 */
export default defineBundledChannelSetupEntry({
  importMetaUrl: import.meta.url,
  plugin: { specifier: "./src/channel.js", exportName: "sellerclawUiChannelPlugin" },
});
