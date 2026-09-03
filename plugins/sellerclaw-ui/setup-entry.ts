import { defineBundledChannelSetupEntry } from "openclaw/plugin-sdk/channel-entry-contract";

/**
 * The setup-only entry, used by onboarding and channel-config surfaces.
 *
 * Same reason as `index.ts`: a bundled channel's setup entry must carry the
 * `bundled-channel-setup-entry` contract or the native scan skips it with a warning. The channel
 * itself is passed by reference so this entry can be imported without loading it.
 */
export default defineBundledChannelSetupEntry({
  importMetaUrl: import.meta.url,
  plugin: { specifier: "./src/channel.js", exportName: "sellerclawUiChannelPlugin" },
});
