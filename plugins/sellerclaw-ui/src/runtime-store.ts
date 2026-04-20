import { createPluginRuntimeStore } from "openclaw/plugin-sdk/runtime-store";
import type { PluginRuntime } from "openclaw/plugin-sdk/runtime-store";

const store = createPluginRuntimeStore("sellerclaw-ui: plugin runtime not initialized");

export function setRuntime(runtime: PluginRuntime): void {
  store.setRuntime(runtime);
}

export function getRuntime(): PluginRuntime {
  return store.getRuntime();
}
