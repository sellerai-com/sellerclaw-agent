import { describe, expect, it } from "vitest";
import { resolveSellerclawAuthToken, resolveSellerclawBaseUrl } from "../config.js";

describe("resolveSellerclawAuthToken", () => {
  it("returns authToken from plugins.entries.sellerclaw-web-search", () => {
    const key = resolveSellerclawAuthToken({
      plugins: {
        entries: {
          "sellerclaw-web-search": {
            config: { webSearch: { authToken: "tok", baseUrl: "https://api.example" } },
          },
        },
      },
    } as never);
    expect(key).toBe("tok");
  });

  it("returns undefined when authToken missing", () => {
    expect(
      resolveSellerclawAuthToken({
        plugins: {
          entries: {
            "sellerclaw-web-search": {
              config: { webSearch: { baseUrl: "https://api.example" } },
            },
          },
        },
      } as never),
    ).toBeUndefined();
  });
});

describe("resolveSellerclawBaseUrl", () => {
  it("strips trailing slash", () => {
    expect(
      resolveSellerclawBaseUrl({
        plugins: {
          entries: {
            "sellerclaw-web-search": {
              config: { webSearch: { authToken: "x", baseUrl: "https://api.example/" } },
            },
          },
        },
      } as never),
    ).toBe("https://api.example");
  });

  it("returns empty string when baseUrl missing", () => {
    expect(
      resolveSellerclawBaseUrl({
        plugins: {
          entries: {
            "sellerclaw-web-search": {
              config: { webSearch: { authToken: "x" } },
            },
          },
        },
      } as never),
    ).toBe("");
  });
});
