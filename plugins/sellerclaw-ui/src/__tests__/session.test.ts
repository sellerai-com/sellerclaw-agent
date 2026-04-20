import { describe, expect, it } from "vitest";

import { resolveSessionKey } from "../channel.js";

describe("resolveSessionKey", () => {
  it("extracts sessionKey from top level", () => {
    expect(resolveSessionKey({ sessionKey: "a" })).toBe("a");
  });

  it("extracts session fallback", () => {
    expect(resolveSessionKey({ session: "b" })).toBe("b");
  });

  it("extracts to fallback", () => {
    expect(resolveSessionKey({ to: "c" })).toBe("c");
  });

  it("prefers sessionKey over session and to", () => {
    expect(
      resolveSessionKey({
        sessionKey: "first",
        session: "second",
        to: "third",
      }),
    ).toBe("first");
  });

  it("prefers session over to when sessionKey absent", () => {
    expect(
      resolveSessionKey({
        session: "s",
        to: "t",
      }),
    ).toBe("s");
  });

  it("extracts delivery.sessionKey", () => {
    expect(resolveSessionKey({ delivery: { sessionKey: "d" } })).toBe("d");
  });

  it("extracts context.sessionKey", () => {
    expect(resolveSessionKey({ context: { sessionKey: "e" } })).toBe("e");
  });

  it("prefers top-level over nested delivery", () => {
    expect(
      resolveSessionKey({
        sessionKey: "top",
        delivery: { sessionKey: "nested" },
      }),
    ).toBe("top");
  });

  it("uses delivery when no top-level key", () => {
    expect(
      resolveSessionKey({
        delivery: { sessionKey: "nested" },
        context: { sessionKey: "ctx" },
      }),
    ).toBe("nested");
  });

  it("returns null when no session key anywhere", () => {
    expect(resolveSessionKey({})).toBeNull();
    expect(resolveSessionKey({ delivery: {}, context: {} })).toBeNull();
  });
});
