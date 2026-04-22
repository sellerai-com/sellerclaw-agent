import { vi } from "vitest";

vi.mock("openclaw/plugin-sdk/security-runtime", () => ({
  wrapWebContent: (s: string, _source: string) => s,
}));

vi.mock("openclaw/plugin-sdk/provider-web-search-contract", () => ({
  createWebSearchProviderContractFields: () => ({
    inactiveSecretPaths: ["plugins.entries.sellerclaw-web-search.config.webSearch.authToken"],
    getCredentialValue: () => undefined,
    setCredentialValue: () => {},
    getConfiguredCredentialValue: () => undefined,
    setConfiguredCredentialValue: () => {},
  }),
}));

vi.mock("openclaw/plugin-sdk/secret-input", () => ({
  normalizeResolvedSecretInputString: ({ value }: { value: unknown }) => value,
  normalizeSecretInput: (v: unknown) => {
    if (typeof v === "string" && v.trim()) {
      return v;
    }
    return undefined;
  },
}));

vi.mock("openclaw/plugin-sdk/text-runtime", () => ({
  normalizeOptionalString: (v: unknown) => (typeof v === "string" ? v : undefined),
}));
