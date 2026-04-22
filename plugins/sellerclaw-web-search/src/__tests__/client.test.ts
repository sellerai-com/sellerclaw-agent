import { describe, expect, it, vi } from "vitest";
import { runSellerclawWebSearch, SELLERCLAW_WEB_SEARCH_TIMEOUT_MS } from "../client.js";

const cfg = {
  plugins: {
    entries: {
      "sellerclaw-web-search": {
        config: { webSearch: { authToken: "agent-token", baseUrl: "https://api.example" } },
      },
    },
  },
} as never;

describe("runSellerclawWebSearch", () => {
  it("POSTs JSON and maps a raw results payload to the web_search envelope", async () => {
    const fetchFn = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      text: async () =>
        JSON.stringify({
          results: [
            { title: "T", url: "https://u", content: "snippet text" },
          ],
        }),
    });

    const out = await runSellerclawWebSearch({
      cfg,
      query: "q1",
      fetchFn,
    });

    expect(fetchFn).toHaveBeenCalledTimes(1);
    const [url, init] = fetchFn.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("https://api.example/research/web-search");
    expect(init.method).toBe("POST");
    expect(init.headers).toMatchObject({
      Authorization: "Bearer agent-token",
      "X-Client-Source": "openclaw-sellerclaw-plugin",
    });
    const body = JSON.parse(init.body as string);
    expect(body.query).toBe("q1");
    expect(body.max_results).toBe(5);

    expect(out.provider).toBe("sellerclaw-web-search");
    expect(out.query).toBe("q1");
    expect(Array.isArray(out.results)).toBe(true);
    expect((out.results as { url: string }[])[0].url).toBe("https://u");
  });

  it("passes through a full WebSearchProvider-shaped payload", async () => {
    const envelope = {
      query: "x",
      provider: "tavily",
      count: 0,
      tookMs: 1,
      externalContent: {
        untrusted: true,
        source: "web_search",
        provider: "tavily",
        wrapped: true,
      },
      results: [],
    };
    const fetchFn = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      text: async () => JSON.stringify(envelope),
    });
    const out = await runSellerclawWebSearch({ cfg, query: "x", fetchFn });
    expect(out).toEqual(envelope);
  });

  it("formats 4xx errors with detail", async () => {
    const fetchFn = vi.fn().mockResolvedValue({
      ok: false,
      status: 402,
      text: async () => JSON.stringify({ detail: "insufficient credits" }),
    });
    await expect(runSellerclawWebSearch({ cfg, query: "q", fetchFn })).rejects.toThrow(
      /402.*insufficient credits/,
    );
  });

  it("aborts when the request hits the timeout", async () => {
    const fetchFn = vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
      return new Promise<Response>((_resolve, reject) => {
        const signal = init?.signal;
        if (!signal) {
          reject(new Error("expected AbortSignal"));
          return;
        }
        signal.addEventListener("abort", () => {
          const err = new Error("Aborted");
          err.name = "AbortError";
          reject(err);
        });
      });
    });
    await expect(
      runSellerclawWebSearch({
        cfg,
        query: "q",
        fetchFn,
        timeoutMs: 1,
      }),
    ).rejects.toThrow();
  });

  it("uses custom timeout bound", () => {
    expect(SELLERCLAW_WEB_SEARCH_TIMEOUT_MS).toBe(30_000);
  });
});
