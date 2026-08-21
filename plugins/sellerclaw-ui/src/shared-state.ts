/**
 * Process-wide state slots that survive plugin module re-instantiation.
 *
 * OpenClaw builds a fresh module loader for every plugin-registry pass
 * (`createPluginModuleLoader` is called inside the load, so its loader cache — and with it the
 * evaluated module — is per-pass). Anything kept in a module-level `let`/`Map` therefore starts
 * empty in each pass, while hooks from the newest pass are the only ones the runtime dispatches.
 *
 * That silently breaks any state handed between two hook firings: the completion guard captures
 * an answer in `before_agent_finalize` and consumes it in `message_sending`, and it keeps a
 * suppression window open for tens of seconds afterwards. A registry rebuild in between would
 * leave the live hooks looking at an empty map — the raw subagent envelope would sail through
 * exactly as if the guard were not installed.
 *
 * Anchoring that state on `globalThis` makes it independent of how the loader caches modules;
 * OpenClaw itself pins its hook-runner state the same way.
 */

const SHARED_STATE_KEY = "__sellerclawUiPluginSharedState__";

type SharedStateSlot = Record<string, unknown>;

function slot(): SharedStateSlot {
  const holder = globalThis as unknown as Record<string, SharedStateSlot | undefined>;
  const existing = holder[SHARED_STATE_KEY];
  if (existing) {
    return existing;
  }
  const created: SharedStateSlot = {};
  holder[SHARED_STATE_KEY] = created;
  return created;
}

/**
 * Returns the one shared value for `key`, creating it on first use.
 *
 * Callers hold the returned object (a Map, a Set, a counter box) for the lifetime of their
 * module instance; a later instance asking for the same key gets the very same object.
 */
export function getSharedState<T>(key: string, create: () => T): T {
  const state = slot();
  if (!(key in state)) {
    state[key] = create();
  }
  return state[key] as T;
}

/** Test-only: drop every slot so cases cannot leak into each other. */
export function __resetSharedState(): void {
  const holder = globalThis as unknown as Record<string, SharedStateSlot | undefined>;
  holder[SHARED_STATE_KEY] = undefined;
}
