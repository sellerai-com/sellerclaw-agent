import { vi } from "vitest";

vi.mock("openclaw/plugin-sdk/core", () => ({
  createChannelPluginBase: (cfg: unknown) => cfg,
  createChatChannelPlugin: (cfg: unknown) => cfg,
}));

/** Shared holder — mirrors SDK slot used by runtime-store.ts (single module instance). */
let mockRuntimeHolder: unknown;

vi.mock("openclaw/plugin-sdk/runtime-store", () => ({
  createPluginRuntimeStore: (_msg: string) => ({
    setRuntime(v: unknown) {
      mockRuntimeHolder = v;
    },
    clearRuntime() {
      mockRuntimeHolder = undefined;
    },
    tryGetRuntime() {
      return mockRuntimeHolder ?? null;
    },
    getRuntime() {
      if (mockRuntimeHolder === undefined) {
        throw new Error("PluginRuntime not set");
      }
      return mockRuntimeHolder;
    },
  }),
}));

vi.mock("openclaw/plugin-sdk/channel-inbound", () => ({
  dispatchInboundDirectDmWithRuntime: vi.fn().mockResolvedValue(undefined),
}));

vi.mock("openclaw/plugin-sdk/webhook-ingress", () => ({
  readJsonWebhookBodyOrReject: vi.fn(),
}));

vi.mock("openclaw/plugin-sdk/media-store", () => ({
  saveMediaBuffer: vi.fn().mockResolvedValue({
    id: "media-id",
    path: "/home/node/.openclaw/media/inbound/media-id",
    size: 0,
    contentType: "application/octet-stream",
  }),
  resolveMediaBufferPath: vi
    .fn()
    .mockResolvedValue("/home/node/.openclaw/media/inbound/media-id"),
}));
