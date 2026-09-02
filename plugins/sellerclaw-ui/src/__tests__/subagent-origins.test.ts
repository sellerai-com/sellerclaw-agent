import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  __resetSubagentOrigins,
  lookupSubagentOrigin,
  rememberSubagentOrigin,
} from "../subagent-origins.js";

const CHAT_ID = "48729abf-b759-46bf-8802-6768891edc7e";
const REQUESTER = `agent:supervisor:sellerclaw-ui:direct:${CHAT_ID}`;
const CHILD = "agent:marketing:subagent:9d1a1f0c-6b3f-4a5c-9f0e-2b7d5c1e4a88";
const HOUR_MS = 60 * 60 * 1000;

beforeEach(__resetSubagentOrigins);
afterEach(__resetSubagentOrigins);

describe("subagent origins", () => {
  it("remembers which chat a specialist works for, and for whom", () => {
    const origin = rememberSubagentOrigin({
      childSessionKey: CHILD,
      agentId: "marketing",
      requesterSessionKey: REQUESTER,
      requesterTarget: `sellerclaw-ui:direct:${CHAT_ID}`,
    });

    expect(origin).toEqual({
      chatId: CHAT_ID,
      requesterSessionKey: REQUESTER,
      agentId: "marketing",
      parentAgentId: "supervisor",
      recordedAt: expect.any(Number) as unknown as number,
    });
    expect(lookupSubagentOrigin(CHILD)).toEqual(origin);
  });

  it("takes the chat from the spawn's target when the requester key carries none", () => {
    // A spawn whose requester session is internal, but whose origin still names the chat.
    const origin = rememberSubagentOrigin({
      childSessionKey: CHILD,
      agentId: "marketing",
      requesterSessionKey: "agent:supervisor:task:ad-review",
      requesterTarget: `sellerclaw-ui:direct:${CHAT_ID}`,
    });

    expect(origin?.chatId).toBe(CHAT_ID);
    expect(origin?.parentAgentId).toBe("supervisor");
  });

  it.each([
    ["a cron run", { requesterSessionKey: "agent:supervisor:cron:scan:run:3", requesterTarget: null }],
    ["another channel", { requesterSessionKey: "agent:supervisor:telegram:direct:42", requesterTarget: "telegram:direct:42" }],
    ["no requester at all", { requesterSessionKey: "", requesterTarget: null }],
  ])("does not follow %s", (_label, over) => {
    expect(
      rememberSubagentOrigin({ childSessionKey: CHILD, agentId: "marketing", ...over }),
    ).toBeNull();
    expect(lookupSubagentOrigin(CHILD)).toBeNull();
  });

  it("forgets a spawn once it is older than the window", () => {
    const spawnedAt = 1_000_000;
    rememberSubagentOrigin({
      childSessionKey: CHILD,
      agentId: "marketing",
      requesterSessionKey: REQUESTER,
      now: spawnedAt,
    });

    expect(lookupSubagentOrigin(CHILD, spawnedAt + 11 * HOUR_MS)).not.toBeNull();
    expect(lookupSubagentOrigin(CHILD, spawnedAt + 13 * HOUR_MS)).toBeNull();
  });

  it("keeps the newest spawns when a busy gateway floods it", () => {
    for (let i = 0; i < 260; i += 1) {
      rememberSubagentOrigin({
        childSessionKey: `agent:marketing:subagent:${i}`,
        agentId: "marketing",
        requesterSessionKey: REQUESTER,
      });
    }

    expect(lookupSubagentOrigin("agent:marketing:subagent:0")).toBeNull();
    expect(lookupSubagentOrigin("agent:marketing:subagent:259")).not.toBeNull();
    expect(lookupSubagentOrigin("agent:marketing:subagent:100")).not.toBeNull();
  });

  it("passes the chat down a nested delegation, under the specialist that asked", () => {
    rememberSubagentOrigin({
      childSessionKey: CHILD,
      agentId: "marketing",
      requesterSessionKey: REQUESTER,
    });

    const grandchild = rememberSubagentOrigin({
      childSessionKey: "agent:scout:subagent:0f6b2a71-4c19-4a52-9d0a-8f4c2b6e1d33",
      agentId: "scout",
      requesterSessionKey: CHILD,
    });

    expect(grandchild?.chatId).toBe(CHAT_ID);
    // Addressed with the chat's session, not the parent specialist's — that one names no chat.
    expect(grandchild?.requesterSessionKey).toBe(REQUESTER);
    expect(grandchild?.parentAgentId).toBe("marketing");
  });
});
