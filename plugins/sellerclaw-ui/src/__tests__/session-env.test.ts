import { describe, expect, it, vi } from "vitest";

import type { OpenClawPluginApi } from "openclaw/plugin-sdk/core";

import { SESSION_KEY_ENV, registerSessionEnv, sessionEnvForToolCall } from "../session-env.js";

const CHAT_SESSION = "agent:supervisor:sellerclaw-ui:direct:48729abf-b759-46bf-8802-6768891edc7e";
const EXECUTOR_SESSION = "agent:ebay:subagent:cd0df525-f55c-45a9-b328-02baaa8ece25";

describe("session-env", () => {
  it.each([
    ["a chat run", CHAT_SESSION],
    ["an executor run", EXECUTOR_SESSION],
  ])("names the session in the shell of %s", (_label, sessionKey) => {
    const result = sessionEnvForToolCall(
      { toolName: "exec", params: { command: "sellerclaw team-tasks list" } },
      { sessionKey },
    );

    expect(result).toEqual({
      params: {
        command: "sellerclaw team-tasks list",
        env: { [SESSION_KEY_ENV]: sessionKey },
      },
    });
  });

  it("keeps whatever else the call already put in the environment", () => {
    const result = sessionEnvForToolCall(
      { toolName: "exec", params: { command: "true", env: { LANG: "C", workdir: "/tmp" } } },
      { sessionKey: CHAT_SESSION },
    );

    expect(result?.params.env).toEqual({
      LANG: "C",
      workdir: "/tmp",
      [SESSION_KEY_ENV]: CHAT_SESSION,
    });
  });

  it("leaves a call alone that already names this session", () => {
    expect(
      sessionEnvForToolCall(
        { toolName: "exec", params: { command: "true", env: { [SESSION_KEY_ENV]: CHAT_SESSION } } },
        { sessionKey: CHAT_SESSION },
      ),
    ).toBeUndefined();
  });

  it.each([
    ["another tool", { toolName: "read", params: { path: "/x" } }, { sessionKey: CHAT_SESSION }],
    ["no session in the context", { toolName: "exec", params: { command: "true" } }, {}],
    ["a blank session", { toolName: "exec", params: { command: "true" } }, { sessionKey: "  " }],
    ["no context at all", { toolName: "exec", params: { command: "true" } }, undefined],
  ])("adds nothing for %s", (_label, event, ctx) => {
    expect(sessionEnvForToolCall(event, ctx)).toBeUndefined();
  });

  it("registers on before_tool_call and answers through it", () => {
    const handlers = new Map<string, (event: unknown, ctx?: unknown) => unknown>();
    const api = {
      on: vi.fn((name: string, handler: (event: unknown, ctx?: unknown) => unknown) => {
        handlers.set(name, handler);
      }),
    } as unknown as OpenClawPluginApi;

    registerSessionEnv(api);

    const handler = handlers.get("before_tool_call");
    expect(handler).toBeDefined();
    expect(
      handler?.({ toolName: "exec", params: { command: "true" } }, { sessionKey: CHAT_SESSION }),
    ).toEqual({ params: { command: "true", env: { [SESSION_KEY_ENV]: CHAT_SESSION } } });
  });

  it("does nothing on a registry pass that offers no hooks", () => {
    expect(() => registerSessionEnv({} as OpenClawPluginApi)).not.toThrow();
  });
});
