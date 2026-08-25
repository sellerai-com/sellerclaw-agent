import { beforeEach, describe, expect, it, vi } from "vitest";

import type { OpenClawPluginApi } from "openclaw/plugin-sdk/core";

import {
  __resetHookRegistrationState,
  registerLifecycleHooks,
  withPerPassHooks,
} from "../hook-registration.js";

/**
 * The regression these tests pin down: OpenClaw 2026.8 rebuilds plugin registries per pass and
 * each activation replaces the hook registry, so hooks registered only in `registerFull` (first
 * pass) silently stop existing. Lifecycle hooks must land on EVERY pass that offers `api.on`.
 */

function buildApi(): { api: OpenClawPluginApi; on: ReturnType<typeof vi.fn>; warn: ReturnType<typeof vi.fn> } {
  const on = vi.fn();
  const warn = vi.fn();
  const api = {
    config: {},
    logger: { info: vi.fn(), warn, error: vi.fn() },
    on,
  } as unknown as OpenClawPluginApi;
  return { api, on, warn };
}

function registeredHookNames(on: ReturnType<typeof vi.fn>): Set<string> {
  return new Set(on.mock.calls.map((call) => String(call[0])));
}

beforeEach(() => {
  __resetHookRegistrationState();
});

describe("registerLifecycleHooks", () => {
  it("registers the guard and run-outcome hooks on an api that offers `on`", () => {
    const { api, on } = buildApi();
    registerLifecycleHooks(api);
    const names = registeredHookNames(on);
    // Completion-delivery guard: answer capture, messaging-tool tracker, interception.
    expect(names).toContain("before_tool_call");
    expect(names).toContain("message_sending");
    // Never ``before_agent_finalize``: registering it makes the runtime defer the whole visible
    // reply stream to the end of the run, which is the live-streaming regression it caused once.
    expect(names).not.toContain("before_agent_finalize");
    // The guard's decision point, the run-outcome tracker and the reasoning relay all listen
    // on agent_end — it is the only hook carrying a non-dispatched run's transcript.
    expect(on.mock.calls.filter((call) => call[0] === "agent_end").length).toBe(3);
    // The guard reads each run's visible text off llm_output.
    expect(on.mock.calls.filter((call) => call[0] === "llm_output").length).toBe(1);
  });

  it("registers again on a fresh api — each registry pass must get its own hooks", () => {
    const first = buildApi();
    const second = buildApi();
    registerLifecycleHooks(first.api);
    registerLifecycleHooks(second.api);
    expect(registeredHookNames(second.on)).toEqual(registeredHookNames(first.on));
    expect(second.on).toHaveBeenCalledTimes(first.on.mock.calls.length);
  });

  it("does not double-register when handed the same api twice", () => {
    const { api, on } = buildApi();
    registerLifecycleHooks(api);
    const callsAfterFirst = on.mock.calls.length;
    registerLifecycleHooks(api);
    expect(on.mock.calls.length).toBe(callsAfterFirst);
  });

  it("silently skips a metadata-only api without `on`", () => {
    const warn = vi.fn();
    const api = { config: {}, logger: { info: vi.fn(), warn, error: vi.fn() } } as unknown as OpenClawPluginApi;
    expect(() => registerLifecycleHooks(api)).not.toThrow();
    expect(warn).not.toHaveBeenCalled();
  });

  it("logs a warn-level liveness line with an increasing pass number", () => {
    const first = buildApi();
    const second = buildApi();
    registerLifecycleHooks(first.api);
    registerLifecycleHooks(second.api);
    expect(first.warn).toHaveBeenCalledWith("sellerclaw-ui: lifecycle hooks registered (pass #1)");
    expect(second.warn).toHaveBeenCalledWith("sellerclaw-ui: lifecycle hooks registered (pass #2)");
  });
});

describe("withPerPassHooks", () => {
  it("keeps the entry's own register and adds hook registration to every pass", () => {
    const baseRegister = vi.fn();
    const entry = withPerPassHooks({ register: baseRegister });
    const first = buildApi();
    const second = buildApi();
    entry.register(first.api);
    entry.register(second.api);
    expect(baseRegister).toHaveBeenCalledTimes(2);
    expect(registeredHookNames(first.on).size).toBeGreaterThan(0);
    expect(registeredHookNames(second.on).size).toBeGreaterThan(0);
  });

  it("still runs the base register for passes without `on`", () => {
    const baseRegister = vi.fn();
    const entry = withPerPassHooks({ register: baseRegister });
    const api = { config: {}, logger: { warn: vi.fn() } } as unknown as OpenClawPluginApi;
    entry.register(api);
    expect(baseRegister).toHaveBeenCalledWith(api);
  });
});
