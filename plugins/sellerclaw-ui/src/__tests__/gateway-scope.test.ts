import { getPluginRuntimeGatewayRequestScope } from "openclaw/plugin-sdk/plugin-runtime";
import { afterEach, describe, expect, it, vi } from "vitest";

import { holdRouteScopeAuthorityForTurn } from "../gateway-scope.js";

const scopeMock = vi.mocked(getPluginRuntimeGatewayRequestScope);

afterEach(() => {
  scopeMock.mockReset();
  scopeMock.mockReturnValue(undefined);
});

describe("holdRouteScopeAuthorityForTurn", () => {
  it("keeps the route's authority current after the 202, until the turn is released", () => {
    // What OpenClaw's route scope says once the response has ended.
    const scope = { hasCurrentClientAuthority: () => false };
    scopeMock.mockReturnValue(scope as never);

    const release = holdRouteScopeAuthorityForTurn();
    expect(scope.hasCurrentClientAuthority()).toBe(true);

    release();
    expect(scope.hasCurrentClientAuthority()).toBe(false);
  });

  it.each([
    ["no scope at all", undefined],
    ["a scope without the authority check", { context: {} }],
  ])("leaves %s alone", (_label, scope) => {
    scopeMock.mockReturnValue(scope as never);

    const release = holdRouteScopeAuthorityForTurn();

    expect(() => release()).not.toThrow();
    expect(scope).toEqual(scope === undefined ? undefined : { context: {} });
  });
});
