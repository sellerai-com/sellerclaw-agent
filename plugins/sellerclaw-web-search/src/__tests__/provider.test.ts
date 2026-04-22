import { describe, expect, it, vi } from "vitest";
import { createSellerclawWebSearchProvider } from "../provider.js";

vi.mock("../client.js", () => ({
  runSellerclawWebSearch: vi.fn().mockResolvedValue({ provider: "brave", query: "ok" }),
}));

describe("createSellerclawWebSearchProvider", () => {
  it("createTool execute forwards to runSellerclawWebSearch", async () => {
    const { runSellerclawWebSearch } = await import("../client.js");
    const provider = createSellerclawWebSearchProvider();
    expect(provider.id).toBe("sellerclaw-web-search");
    const tool = provider.createTool!({
      config: {
        plugins: {
          entries: {
            "sellerclaw-web-search": {
              config: {
                webSearch: { authToken: "t", baseUrl: "https://api.example" },
              },
            },
          },
        },
      },
    });
    const out = await tool.execute({ query: "hello", count: 3 });
    expect(runSellerclawWebSearch).toHaveBeenCalledWith({
      cfg: expect.anything(),
      query: "hello",
      maxResults: 3,
    });
    expect(out).toEqual({ provider: "brave", query: "ok" });
  });
});
