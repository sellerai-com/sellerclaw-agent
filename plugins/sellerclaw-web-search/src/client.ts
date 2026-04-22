import type { OpenClawConfig } from "openclaw/plugin-sdk/config-runtime";
import { wrapWebContent } from "openclaw/plugin-sdk/security-runtime";
import { resolveSellerclawAuthToken, resolveSellerclawBaseUrl } from "./config.js";

const DEFAULT_COUNT = 5;
export const SELLERCLAW_WEB_SEARCH_TIMEOUT_MS = 30_000;

export async function runSellerclawWebSearch(params: {
  cfg?: OpenClawConfig;
  query: string;
  maxResults?: number;
  fetchFn?: typeof fetch;
  timeoutMs?: number;
}): Promise<Record<string, unknown>> {
  const authToken = resolveSellerclawAuthToken(params.cfg);
  if (!authToken) {
    throw new Error(
      "web_search (sellerclaw-web-search) needs plugins.entries.sellerclaw-web-search.config.webSearch.authToken.",
    );
  }
  const baseUrl = resolveSellerclawBaseUrl(params.cfg);
  if (!baseUrl) {
    throw new Error(
      "web_search (sellerclaw-web-search) needs plugins.entries.sellerclaw-web-search.config.webSearch.baseUrl.",
    );
  }
  const count =
    typeof params.maxResults === "number" && Number.isFinite(params.maxResults)
      ? Math.max(1, Math.min(20, Math.floor(params.maxResults)))
      : DEFAULT_COUNT;

  const url = `${baseUrl}/research/web-search`;
  const started = Date.now();
  const fetchImpl = params.fetchFn ?? globalThis.fetch;
  const timeoutMs = params.timeoutMs ?? SELLERCLAW_WEB_SEARCH_TIMEOUT_MS;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetchImpl(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${authToken}`,
        "X-Client-Source": "openclaw-sellerclaw-plugin",
      },
      body: JSON.stringify({ query: params.query, max_results: count }),
      signal: controller.signal,
    });
    const tookMs = Date.now() - started;
    const text = await res.text();
    let payload: unknown;
    try {
      payload = text ? JSON.parse(text) : {};
    } catch {
      throw new Error(
        `SellerClaw web search returned non-JSON (${res.status}): ${text.slice(0, 200)}`,
      );
    }
    if (!res.ok) {
      const detail = extractErrorDetail(payload, text);
      throw new Error(`SellerClaw web search failed (${res.status}): ${detail}`);
    }
    return normalizeSellerclawResponse(payload, params.query, tookMs);
  } finally {
    clearTimeout(timer);
  }
}

function extractErrorDetail(payload: unknown, rawText: string): string {
  if (payload && typeof payload === "object" && "detail" in payload) {
    const d = (payload as { detail?: unknown }).detail;
    if (typeof d === "string") {
      return d;
    }
    try {
      return JSON.stringify(d);
    } catch {
      return rawText.slice(0, 300);
    }
  }
  return rawText.slice(0, 300) || "request failed";
}

function normalizeSellerclawResponse(
  payload: unknown,
  fallbackQuery: string,
  tookMs: number,
): Record<string, unknown> {
  if (payload && typeof payload === "object") {
    const p = payload as Record<string, unknown>;
    if (
      p.externalContent &&
      typeof p.externalContent === "object" &&
      Array.isArray(p.results)
    ) {
      return p;
    }
  }
  return mapRawToEnvelope(payload, fallbackQuery, tookMs);
}

function mapRawToEnvelope(raw: unknown, query: string, tookMs: number): Record<string, unknown> {
  const payload = raw && typeof raw === "object" ? (raw as Record<string, unknown>) : {};
  const rawResults = Array.isArray(payload.results) ? payload.results : [];
  const results = rawResults.map((item: unknown) => {
    const r = item && typeof item === "object" ? (item as Record<string, unknown>) : {};
    const title = typeof r.title === "string" ? wrapWebContent(r.title, "web_search") : "";
    const url = typeof r.url === "string" ? r.url : "";
    const snippetRaw =
      typeof r.snippet === "string"
        ? r.snippet
        : typeof r.content === "string"
          ? r.content
          : "";
    const snippet = snippetRaw ? wrapWebContent(snippetRaw, "web_search") : "";
    const row: Record<string, unknown> = { title, url, snippet };
    if (typeof r.score === "number") {
      row.score = r.score;
    }
    if (typeof r.published === "string") {
      row.published = r.published;
    }
    return row;
  });

  const out: Record<string, unknown> = {
    query: typeof payload.query === "string" ? payload.query : query,
    provider: "sellerclaw-web-search",
    count: results.length,
    tookMs,
    externalContent: {
      untrusted: true,
      source: "web_search",
      provider: "sellerclaw-web-search",
      wrapped: true,
    },
    results,
  };
  if (typeof payload.answer === "string" && payload.answer) {
    out.answer = wrapWebContent(payload.answer, "web_search");
  }
  return out;
}
