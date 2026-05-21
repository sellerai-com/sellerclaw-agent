import { describe, expect, it } from "vitest";

import { isRuntimeToolActivityLine, stripRuntimeToolActivityLines } from "../tool-activity-filter.js";

describe("isRuntimeToolActivityLine", () => {
  it.each([
    ["🤖 Subagents", "subagents-header"],
    [
      "🧾 Session History: session agent:marketing:subagent:87abb016-1111-2222-3333-444444444444, limit 20",
      "session-history",
    ],
    ["🛠️ sellerclaw agent-orders list failed", "exec-compact-failed"],
    ["🛠️ Exec: sellerclaw agent-orders list", "exec-labeled"],
    ["🛠️ elevated · sellerclaw agent-sales-channels list", "exec-with-flags"],
    ["🗂️ Sessions: kinds all, limit 20", "sessions-list"],
    ["🧑‍🔧 Sub-agent: marketing · summarize campaign", "subagent-spawn"],
    ["📊 Session Status: agent:supervisor:sellerclaw-ui:direct:c1", "session-status"],
    ["> 🛠️ sellerclaw agent-orders list", "blockquote-exec"],
    ["session agent:marketing:subagent:87abb016-1111-2222-3333-444444444444, limit 20", "bare-session-key"],
    ["tool call: sessions_history", "internal-channel-line"],
  ])("detects runtime activity line (%s)", (line, _id) => {
    expect(isRuntimeToolActivityLine(line)).toBe(true);
  });

  it.each([
    ["Сейчас проверю ваши заказы.", "user-prose"],
    ["## Campaign overview", "markdown-heading"],
    ["- first bullet item", "markdown-list"],
    ["Here is what I found about Google Ads.", "plain-prose"],
    ["Use the 🛠️ emoji in marketing copy", "inline-emoji-not-at-start"],
  ])("allows user-facing line (%s)", (line, _id) => {
    expect(isRuntimeToolActivityLine(line)).toBe(false);
  });
});

describe("stripRuntimeToolActivityLines", () => {
  it("removes known runtime footers and keeps user prose", () => {
    const raw = [
      "Сейчас посмотрю ваши заказы.",
      "🤖 Subagents",
      "🧾 Session History: session agent:marketing:subagent:87abb016-1111-2222-3333-444444444444, limit 20",
      "🛠️ sellerclaw agent-orders list failed",
      "Вот что удалось найти:",
      "- два новых заказа",
    ].join("\n");

    expect(stripRuntimeToolActivityLines(raw)).toBe(
      ["Сейчас посмотрю ваши заказы.", "Вот что удалось найти:", "- два новых заказа"].join("\n"),
    );
  });

  it("returns empty string when the chunk is only runtime activity", () => {
    expect(stripRuntimeToolActivityLines("🤖 Subagents\n🛠️ sellerclaw agent-orders list failed")).toBe("");
  });

  it("preserves empty input unchanged", () => {
    expect(stripRuntimeToolActivityLines("")).toBe("");
  });
});
