import { describe, expect, it } from "vitest";

import {
  resolveSellerclawUiAccount,
  sellerclawUiChannelPlugin,
} from "../channel.js";

type OpenClawConfigLike = Parameters<typeof resolveSellerclawUiAccount>[0];

function cfg(section: Record<string, unknown>): OpenClawConfigLike {
  return { channels: { "sellerclaw-ui": section } } as OpenClawConfigLike;
}

type PluginWithInspect = {
  base: {
    setup: {
      inspectAccount: (c: OpenClawConfigLike, id?: string | null) => {
        enabled: boolean;
        configured: boolean;
        tokenStatus: string;
      };
    };
  };
};

type PluginWithStatus = {
  status: {
    defaultRuntime: {
      running: boolean;
      connected: boolean;
      mode: string;
    };
    buildAccountSnapshot: (params: {
      account: ReturnType<typeof resolveSellerclawUiAccount>;
    }) => Promise<Record<string, unknown>>;
  };
};

describe("resolveSellerclawUiAccount", () => {
  it("returns typed account for valid config", () => {
    const result = resolveSellerclawUiAccount(
      cfg({
        apiBaseUrl: "https://api.example.com",
        userId: "550e8400-e29b-41d4-a716-446655440000",
        agentApiKey: "sca_test_key",
        internalWebhookSecret: "hooks-token",
      }),
    );
    expect(result).toEqual({
      apiBaseUrl: "https://api.example.com",
      userId: "550e8400-e29b-41d4-a716-446655440000",
      agentApiKey: "sca_test_key",
      internalWebhookSecret: "hooks-token",
      localAgentBaseUrl: "http://127.0.0.1:8001",
    });
  });

  it("uses explicit localAgentBaseUrl when present", () => {
    const result = resolveSellerclawUiAccount(
      cfg({
        apiBaseUrl: "https://api.example.com",
        userId: "550e8400-e29b-41d4-a716-446655440000",
        agentApiKey: "k",
        internalWebhookSecret: "hooks-token",
        localAgentBaseUrl: "http://127.0.0.1:9999",
      }),
    );
    expect(result.localAgentBaseUrl).toBe("http://127.0.0.1:9999");
  });

  it("ignores extra fields in section", () => {
    const result = resolveSellerclawUiAccount(
      cfg({
        apiBaseUrl: "https://x.com",
        userId: "550e8400-e29b-41d4-a716-446655440000",
        agentApiKey: "k",
        internalWebhookSecret: "s",
        extra: 1,
      } as Record<string, unknown>),
    );
    expect("extra" in result).toBe(false);
  });

  it("throws when apiBaseUrl missing", () => {
    expect(() =>
      resolveSellerclawUiAccount(
        cfg({
          userId: "550e8400-e29b-41d4-a716-446655440000",
          agentApiKey: "k",
          internalWebhookSecret: "s",
        }),
      ),
    ).toThrow("sellerclaw-ui: apiBaseUrl is required");
  });

  it("throws when userId missing", () => {
    expect(() =>
      resolveSellerclawUiAccount(
        cfg({
          apiBaseUrl: "https://x.com",
          agentApiKey: "k",
          internalWebhookSecret: "s",
        }),
      ),
    ).toThrow("sellerclaw-ui: userId is required");
  });

  it("throws when agentApiKey missing or empty", () => {
    expect(() =>
      resolveSellerclawUiAccount(
        cfg({
          apiBaseUrl: "https://x.com",
          userId: "550e8400-e29b-41d4-a716-446655440000",
          agentApiKey: "",
          internalWebhookSecret: "s",
        }),
      ),
    ).toThrow("sellerclaw-ui: agentApiKey is required");
  });

  it("throws when internalWebhookSecret missing or empty", () => {
    expect(() =>
      resolveSellerclawUiAccount(
        cfg({
          apiBaseUrl: "https://x.com",
          userId: "550e8400-e29b-41d4-a716-446655440000",
          agentApiKey: "k",
          internalWebhookSecret: "",
        }),
      ),
    ).toThrow("sellerclaw-ui: internalWebhookSecret is required");
  });

  it("rejects empty or whitespace-only apiBaseUrl", () => {
    expect(() =>
      resolveSellerclawUiAccount(
        cfg({
          apiBaseUrl: "   ",
          userId: "550e8400-e29b-41d4-a716-446655440000",
          agentApiKey: "k",
          internalWebhookSecret: "s",
        }),
      ),
    ).toThrow("sellerclaw-ui: apiBaseUrl is required");
  });

  it("rejects empty userId", () => {
    expect(() =>
      resolveSellerclawUiAccount(
        cfg({
          apiBaseUrl: "https://x.com",
          userId: "",
          agentApiKey: "k",
          internalWebhookSecret: "s",
        }),
      ),
    ).toThrow("sellerclaw-ui: userId is required");
  });

  it("throws when sellerclaw-ui section missing", () => {
    expect(() =>
      resolveSellerclawUiAccount({ channels: {} } as OpenClawConfigLike),
    ).toThrow("sellerclaw-ui: apiBaseUrl is required");
  });
});

describe("inspectAccount", () => {
  const inspect = (sellerclawUiChannelPlugin as PluginWithInspect).base.setup.inspectAccount;

  it("returns enabled for valid config", () => {
    const result = inspect(
      cfg({
        apiBaseUrl: "https://x.com",
        userId: "550e8400-e29b-41d4-a716-446655440000",
        agentApiKey: "k",
        internalWebhookSecret: "s",
      }),
      null,
    );
    expect(result).toEqual({
      enabled: true,
      configured: true,
      tokenStatus: "available",
    });
  });

  it("returns disabled for broken config", () => {
    const result = inspect(
      cfg({
        apiBaseUrl: "",
        userId: "550e8400-e29b-41d4-a716-446655440000",
        agentApiKey: "k",
        internalWebhookSecret: "s",
      }),
      null,
    );
    expect(result.enabled).toBe(false);
    expect(result.configured).toBe(false);
    expect(result.tokenStatus).toBe("missing");
  });
});

describe("messaging.targetResolver", () => {
  /**
   * Regression for the "Unknown target sellerclaw-ui:direct:<uuid>" failure
   * mode: OpenClaw's outbound target resolver
   * (``infra/outbound/target-resolver.ts``) only treats an address as an
   * opaque id when the plugin's ``messaging.targetResolver.looksLikeId``
   * returns true; without it the resolver falls into a directory lookup,
   * gets an empty list (we don't ship one), and raises ``Unknown target``
   * even though the address belongs to the very session that just dispatched
   * the inbound. The agent then burns its message-tool retries and the chat
   * UI surfaces a ``Message failed`` badge.
   */
  type PluginWithMessaging = {
    messaging: {
      inferTargetChatType: (params: { to: string }) => string | undefined;
      targetResolver: {
        hint: string;
        looksLikeId: (raw: string) => boolean;
      };
    };
  };
  const messaging = (sellerclawUiChannelPlugin as unknown as PluginWithMessaging).messaging;

  it("accepts the canonical sellerclaw-ui:direct:<uuid> address as an opaque target id", () => {
    expect(
      messaging.targetResolver.looksLikeId(
        "sellerclaw-ui:direct:9ced2cbb-33a8-4284-b021-4eb77e2b6d81",
      ),
    ).toBe(true);
  });

  it("tolerates surrounding whitespace produced by sloppy agent input", () => {
    expect(
      messaging.targetResolver.looksLikeId(
        "  sellerclaw-ui:direct:9ced2cbb-33a8-4284-b021-4eb77e2b6d81\n",
      ),
    ).toBe(true);
  });

  it("rejects look-alike strings that the resolver must NOT treat as ids", () => {
    // Wrong channel prefix.
    expect(messaging.targetResolver.looksLikeId("telegram:direct:abc")).toBe(false);
    // Right channel, wrong kind segment.
    expect(
      messaging.targetResolver.looksLikeId(
        "sellerclaw-ui:group:9ced2cbb-33a8-4284-b021-4eb77e2b6d81",
      ),
    ).toBe(false);
    // Right shape, non-UUID id (we explicitly disambiguate on UUID to avoid
    // accepting random user input that happens to share the prefix).
    expect(messaging.targetResolver.looksLikeId("sellerclaw-ui:direct:not-a-uuid")).toBe(false);
  });

  it("infers ``direct`` chat type for valid addresses and undefined otherwise", () => {
    expect(
      messaging.inferTargetChatType({
        to: "sellerclaw-ui:direct:9ced2cbb-33a8-4284-b021-4eb77e2b6d81",
      }),
    ).toBe("direct");
    expect(messaging.inferTargetChatType({ to: "sellerclaw-ui:group:x" })).toBeUndefined();
  });

  it("exposes a hint string so error reports tell the operator the expected shape", () => {
    expect(messaging.targetResolver.hint).toMatch(/sellerclaw-ui:direct:/);
  });
});

describe("status", () => {
  const status = (sellerclawUiChannelPlugin as PluginWithStatus).status;

  it("exposes a healthy default runtime for webhook delivery", () => {
    expect(status.defaultRuntime).toEqual({
      running: true,
      connected: true,
      mode: "webhook",
    });
  });

  it("builds a healthy snapshot for configured account", async () => {
    const account = resolveSellerclawUiAccount(
      cfg({
        apiBaseUrl: "https://x.com",
        userId: "550e8400-e29b-41d4-a716-446655440000",
        agentApiKey: "k",
        internalWebhookSecret: "s",
      }),
    );

    await expect(status.buildAccountSnapshot({ account })).resolves.toEqual({
      enabled: true,
      configured: true,
      running: true,
      connected: true,
      mode: "webhook",
    });
  });
});
