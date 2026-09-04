import { createPluginRuntimeStore } from "openclaw/plugin-sdk/runtime-store";
import type { PluginRuntime } from "openclaw/plugin-sdk/runtime-store";

/**
 * The OpenClaw runtime handed to this plugin, in a slot shared by every copy of this module.
 *
 * Keyed by plugin id on purpose. `createPluginRuntimeStore` has two shapes: a bare error string
 * gives a MODULE-LOCAL slot, an options object gives a globally named one. Module-local is wrong
 * for us because this file is evaluated more than once — OpenClaw re-evaluates plugin modules on
 * every registry pass, and the bundled channel entry pulls its pieces in through a loader
 * boundary of its own. With a module-local slot the runtime would be set on one copy and read
 * from another, and the read would throw as if the plugin had never been initialized.
 */
const store = createPluginRuntimeStore({
  pluginId: "sellerclaw-ui",
  errorMessage: "sellerclaw-ui: plugin runtime not initialized",
});

export function setRuntime(runtime: PluginRuntime): void {
  store.setRuntime(runtime);
}

export function getRuntime(): PluginRuntime {
  return store.getRuntime();
}
