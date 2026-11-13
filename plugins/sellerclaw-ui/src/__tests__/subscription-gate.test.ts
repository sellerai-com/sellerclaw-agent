import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { setPluginConfig } from "../channel.js";
import { runEntryFromEvent } from "../cron-sync.js";
import {
  gateDecision,
  inAbout,
  parseStatus,
  readAiSubscriptionSettings,
  readStatus,
  registerSubscriptionGate,
  takeSkippedRun,
} from "../subscription-gate.js";

const NOW = Date.parse("2026-11-12T15:00:00Z");
const CLAUDE = { provider: "claude", statusPath: "/data/ai-subscription/status.json" };
const LIMITED = { state: "limit_reached", resumesAt: NOW + 2 * 3_600_000 + 5 * 60_000 };

describe("gateDecision", () => {
  it("answers the owner while the plan's limit is used up, with how long to wait", () => {
    const decision = gateDecision({ settings: CLAUDE, status: LIMITED, trigger: "user", runId: "r1", now: NOW });

    expect(decision).toEqual({
      claim: true,
      text:
        "Your Claude plan's usage limit is used up, so I'm paused until it resets — in about 2 hours. " +
        "Please send your message again then. Nothing is charged to your SellerClaw credits.",
    });
  });

  it("skips a scheduled run with the reason as its summary", () => {
    const decision = gateDecision({ settings: CLAUDE, status: LIMITED, trigger: "cron", runId: "r2", now: NOW });

    const reason =
      "Skipped: your Claude plan's usage limit is used up (it resets in about 2 hours). " +
      "This task runs again at its next scheduled time.";
    expect(decision).toEqual({ claim: true, text: reason, skipReason: reason });
  });

  it.each([
    ["heartbeat", "r3"],
    ["user", "announce:child-1"],
  ])("keeps %s turns silent so the owner is told once", (trigger, runId) => {
    expect(gateDecision({ settings: CLAUDE, status: LIMITED, trigger, runId, now: NOW })).toEqual({ claim: true });
  });

  it("tells the owner the command that reconnects a lost sign-in", () => {
    const decision = gateDecision({
      settings: { provider: "chatgpt", statusPath: "/x" },
      status: { state: "signed_out", resumesAt: null },
      trigger: "user",
      runId: "r4",
      now: NOW,
    });

    expect(decision.claim).toBe(true);
    expect(decision.text).toBe(
      "I've lost the sign-in to your ChatGPT plan, so I can't work right now. To reconnect, run " +
        "`sellerclaw-agent connect chatgpt` on the computer running your agent.",
    );
  });

  it.each([
    ["no status yet", null],
    ["working", { state: "connected", resumesAt: null }],
    ["limit already reset", { state: "limit_reached", resumesAt: NOW - 1 }],
  ])("lets the turn through when %s", (_label, status) => {
    expect(gateDecision({ settings: CLAUDE, status, trigger: "user", runId: "r5", now: NOW })).toEqual({
      claim: false,
    });
  });

  it("says nothing about a reset time it does not know", () => {
    const decision = gateDecision({
      settings: CLAUDE,
      status: { state: "limit_reached", resumesAt: null },
      trigger: "user",
      runId: "r6",
      now: NOW,
    });

    expect(decision.text).toBe(
      "Your Claude plan's usage limit is used up, so I'm paused until it resets. " +
        "Please send your message again then. Nothing is charged to your SellerClaw credits.",
    );
  });
});

describe("settings and status", () => {
  it("reads the block the bundle writes into the channel config", () => {
    const cfg = { channels: { "sellerclaw-ui": { aiSubscription: CLAUDE } } };

    expect(readAiSubscriptionSettings(cfg)).toEqual(CLAUDE);
    expect(readAiSubscriptionSettings({ channels: { "sellerclaw-ui": {} } })).toBeNull();
    expect(readAiSubscriptionSettings(null)).toBeNull();
  });

  it("parses the agent server's status file and tolerates a broken one", () => {
    expect(parseStatus('{"state":"limit_reached","resumes_at":"2026-11-12T17:05:00+00:00"}')).toEqual(LIMITED);
    expect(parseStatus('{"state":"connected","resumes_at":null}')).toEqual({ state: "connected", resumesAt: null });
    expect(parseStatus("{not json")).toBeNull();
  });

  it("reads the file from disk and treats a missing one as nothing to hold on", () => {
    const dir = mkdtempSync(join(tmpdir(), "subscription-gate-"));
    const path = join(dir, "status.json");

    expect(readStatus(path)).toBeNull();
    writeFileSync(path, '{"state":"signed_out","resumes_at":null}');
    expect(readStatus(path)).toEqual({ state: "signed_out", resumesAt: null });
  });

  it.each([
    [25 * 60_000, "in about 25 minutes"],
    [60_000, "in about 1 minute"],
    [3 * 3_600_000, "in about 3 hours"],
    [4 * 24 * 3_600_000, "in about 4 days"],
  ])("phrases a %d ms wait as %s", (ms, words) => {
    expect(inAbout(ms)).toBe(words);
  });
});

describe("a skipped scheduled run", () => {
  it("is reported to the cloud as skipped, with the gate's reason, once", () => {
    const dir = mkdtempSync(join(tmpdir(), "subscription-gate-cron-"));
    const statusPath = join(dir, "status.json");
    writeFileSync(statusPath, JSON.stringify({ state: "limit_reached", resumes_at: new Date(Date.now() + 3_600_000).toISOString() }));
    setPluginConfig({ channels: { "sellerclaw-ui": { aiSubscription: { provider: "claude", statusPath } } } } as never);
    const handlers = new Map<string, (event: unknown, ctx?: unknown) => unknown>();
    registerSubscriptionGate({ on: (name: string, handler: (event: unknown, ctx?: unknown) => unknown) => handlers.set(name, handler) } as never);

    const claimed = handlers.get("before_agent_reply")?.({}, { trigger: "cron", runId: "run-skipped" }) as {
      handled: boolean;
      reply?: { text: string };
    };
    const entry = runEntryFromEvent({ jobId: "job-1", runAtMs: NOW, runId: "run-skipped", status: "ok", error: "x" });
    const again = runEntryFromEvent({ jobId: "job-1", runAtMs: NOW, runId: "run-skipped", status: "ok" });
    const unrelated = runEntryFromEvent({ jobId: "job-2", runAtMs: NOW, runId: "other-run", status: "ok" });

    expect(claimed.handled).toBe(true);
    expect(entry).toMatchObject({ status: "skipped", summary: claimed.reply?.text });
    expect(entry).not.toHaveProperty("error");
    expect(again).toMatchObject({ status: "ok" });
    expect(unrelated).toMatchObject({ status: "ok" });
    expect(takeSkippedRun("run-skipped")).toBeNull();
  });
});
