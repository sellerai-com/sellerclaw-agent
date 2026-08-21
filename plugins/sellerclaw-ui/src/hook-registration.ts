import type { OpenClawPluginApi } from "openclaw/plugin-sdk/core";

import { registerCompletionDeliveryGuard } from "./completion-delivery.js";
import { logWarn } from "./log.js";
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

/** APIs already given our hooks — one entry per registry build. */
const seenApis = new WeakSet<object>();

/** Counts registrations for the liveness log line; survives passes via the module cache. */
let registrationPass = 0;

export function registerLifecycleHooks(api: OpenClawPluginApi): void {
  if (typeof (api as { on?: unknown }).on !== "function") {
    return;
  }
  if (seenApis.has(api)) {
    return;
  }
  seenApis.add(api);
  registrationPass += 1;
  // Warn level on purpose: the bundle ships `logging.level: "warn"`, and this line is the
  // liveness signal — its absence from a session's logs is how the next silent death gets seen.
  logWarn(api, `sellerclaw-ui: lifecycle hooks registered (pass #${registrationPass})`);
  registerCompletionDeliveryGuard(api);
  registerRunOutcomeTracker(api);
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

/** Test-only: forget seen APIs and the pass counter. */
export function __resetHookRegistrationState(): void {
  registrationPass = 0;
  // WeakSet cannot be cleared; tests use fresh api objects instead.
}
