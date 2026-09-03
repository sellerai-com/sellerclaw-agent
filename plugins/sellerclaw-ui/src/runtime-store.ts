import { createPluginRuntimeStore } from "openclaw/plugin-sdk/runtime-store";
import type { PluginRuntime } from "openclaw/plugin-sdk/runtime-store";

/**
 * The plugin runtime, parked where every evaluation of this module can reach it.
 *
 * Passing ``pluginId`` (rather than the legacy bare error string) is what puts the slot on
 * ``globalThis`` instead of inside this module instance. That matters twice over: OpenClaw
 * re-evaluates plugin modules on every registry pass (see ``shared-state.ts``), and the bundled
 * channel entry loads its sidecars through a boundary of its own, so a second evaluation of this
 * file is normal. With a module-local slot, whichever instance received ``setRuntime`` would be
 * the only one able to answer ``getRuntime`` — and the route handlers asking are in another.
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
