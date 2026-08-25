import type { OpenClawPluginApi } from "openclaw/plugin-sdk/core";

import { registerCompletionDeliveryGuard } from "./completion-delivery.js";
import { logWarn } from "./log.js";
import { registerReasoningRelay } from "./reasoning-relay.js";
import { getSharedState } from "./shared-state.js";
import { registerRunOutcomeTracker } from "./run-outcome.js";

/**
 * Registers the plugin's lifecycle hooks on EVERY plugin-registry pass, not just the "full" one.
 *
 * OpenClaw 2026.8 rebuilds and re-activates plugin registries repeatedly — once at gateway
 * startup and again around embedded agent runs (registration mode "discovery"). Each activation
 * REPLACES the global hook registry (`initializeGlobalHookRunner`: "called on every plugin
 * registry activation"), and the `defineChannelPluginEntry` contract invokes `registerFull` only
 * in "full" mode. Hooks registered there therefore existed only in the first registry and were
 * wiped by the very next activation — which is how the completion-delivery guard and the
 * run-outcome tracker ran zero times on 2026.8.1-beta.2 while every check of the code said they
 * were wired. Plain (non-entry-contract) plugins like openclaw-mem0 survive because their
 * `register()` re-registers hooks on every pass; this module makes our channel entry do the same.
 *
 * `api.on` is present in the "full", "discovery" and "tool-discovery" modes and absent in
 * metadata-only passes — those are skipped silently. Each pass hands a fresh `api` bound to the
 * fresh registry; the WeakSet only guards against the same `api` being offered twice.
 */

/**
 * APIs already given our hooks, and the pass counter behind the liveness line.
 *
 * Both live in a process-wide slot because the plugin module is re-evaluated on every registry
 * pass (see `shared-state.ts`) — a module-level counter would reset to zero each time and report
 * "pass #1" forever, hiding the very re-registration this module exists to make visible.
 */
const seenApis = getSharedState("hook-registration:seen-apis", () => new WeakSet<object>());
const passCounter = getSharedState("hook-registration:pass-counter", () => ({ value: 0 }));

export function registerLifecycleHooks(api: OpenClawPluginApi): void {
  if (typeof (api as { on?: unknown }).on !== "function") {
    return;
  }
  if (seenApis.has(api)) {
    return;
  }
  seenApis.add(api);
  passCounter.value += 1;
  // Warn level on purpose: the bundle ships `logging.level: "warn"`, and this line is the
  // liveness signal — its absence from a session's logs is how the next silent death gets seen.
  logWarn(api, `sellerclaw-ui: lifecycle hooks registered (pass #${passCounter.value})`);
  registerCompletionDeliveryGuard(api);
  registerRunOutcomeTracker(api);
  registerReasoningRelay(api);
}

type RegistrableEntry = { register: (api: OpenClawPluginApi) => void };

/** Wraps a channel entry so lifecycle hooks ride along with every `register()` pass. */
export function withPerPassHooks<T extends RegistrableEntry>(entry: T): T {
  const base = entry.register.bind(entry);
  entry.register = (api: OpenClawPluginApi) => {
    base(api);
    registerLifecycleHooks(api);
  };
  return entry;
}

/** Test-only: reset the pass counter. Seen APIs need no reset — tests use fresh api objects. */
export function __resetHookRegistrationState(): void {
  passCounter.value = 0;
}
