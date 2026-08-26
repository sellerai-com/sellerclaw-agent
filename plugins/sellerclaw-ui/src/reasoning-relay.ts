import type { OpenClawPluginApi } from "openclaw/plugin-sdk/core";

import {
  extractChatIdFromAddress,
  extractTargetFromSessionKey,
  resolveSellerclawUiAccount,
} from "./channel.js";
import { isCompletionRun, isSilentRun, lastUserMessageIndex } from "./completion-run.js";
import { logError, logInfo, logWarn } from "./log.js";
import { postThought, postTurnEnd, postTurnStart } from "./send.js";

/**
 * Reports the thinking of runs this plugin does not dispatch, so the owner's "Thought" panel is
 * not empty for half the answers they get.
 *
 * `replyOptions.onReasoningStream` only reaches a run we start ourselves
 * (`inbound-reply-with-reasoning.ts`). Since OpenClaw 2026.8 the answer to a delegated request
 * comes from an announce / requester-settle run instead — the supervisor is woken when a subagent
 * finishes — and that run's reasoning reached nobody.
 *
 * `agent_end` is the one hook that carries the run's transcript, so it is the only place a
 * non-dispatched run's thinking is readable at all. That is also its limitation: the thinking
 * arrives when the run is over, i.e. just after the answer it explains, never token by token.
 * (`llm_output` looks finer-grained but is not: it fires once per attempt — the whole streamed
 * run — and its `lastAssistant` holds only the final message, so every earlier round's thinking
 * would be lost.)
 *
 * The blocks are posted under a fresh message id whose turn is opened and closed empty. The cloud
 * treats such a turn as a release placeholder and hands its reasoning to the answer of this
 * exchange — the message the `message` tool published moments earlier — or parks it for the
 * answer still being composed. Nothing here needs to know which message that is.
 */

/** One thought item's size cap, mirroring the inbound relay's flush threshold. */
const MAX_THOUGHT_CHARS = 2_000;

/** Bound on how many blocks one run may report, so a pathological run cannot flood the chat. */
const MAX_THOUGHT_BLOCKS = 50;

/**
 * Collect the `thinking` parts of every assistant message the current run wrote.
 *
 * Scoped to messages after the run's trigger (the last user-role message): the transcript also
 * holds every earlier exchange, whose thinking was reported — or deliberately not — long ago.
 */
function readRunThinking(messages: unknown): string[] {
  if (!Array.isArray(messages)) return [];
  const texts: string[] = [];
  for (const message of messages.slice(lastUserMessageIndex(messages) + 1)) {
    if (!message || typeof message !== "object") continue;
    const record = message as { role?: unknown; content?: unknown };
    if (record.role !== "assistant" || !Array.isArray(record.content)) continue;
    for (const part of record.content) {
      if (!part || typeof part !== "object") continue;
      const block = part as { type?: unknown; thinking?: unknown };
      if (block.type !== "thinking" || typeof block.thinking !== "string") continue;
      const text = block.thinking.trim();
      if (text) texts.push(text);
    }
  }
  return texts;
}

/** Split an over-long block on a line boundary so no single thought item is unbounded. */
function chunkThought(text: string): string[] {
  const chunks: string[] = [];
  let rest = text;
  while (rest.length > MAX_THOUGHT_CHARS) {
    const window = rest.slice(0, MAX_THOUGHT_CHARS);
    const newline = window.lastIndexOf("\n");
    const cut = newline > 0 ? newline : MAX_THOUGHT_CHARS;
    chunks.push(rest.slice(0, cut).trim());
    rest = rest.slice(cut);
  }
  const tail = rest.trim();
  if (tail) chunks.push(tail);
  return chunks.filter((chunk) => chunk.length > 0);
}

function asString(value: unknown): string {
  return typeof value === "string" ? value : "";
}

interface AgentEndEvent {
  runId?: unknown;
  messages?: unknown;
}

interface HookContext {
  sessionKey?: unknown;
  runId?: unknown;
  agentId?: unknown;
}

/** Post one run's thinking and close the empty turn that carries it. */
async function relayRunThinking(params: {
  api: OpenClawPluginApi;
  sessionKey: string;
  chatId: string | null;
  agentId: string;
  texts: string[];
}): Promise<void> {
  const { api, sessionKey, chatId, agentId } = params;
  let account;
  try {
    account = resolveSellerclawUiAccount(api.config);
  } catch (err) {
    logError(api, `sellerclaw-ui: reasoning relay cannot resolve account: ${String(err)}`);
    return;
  }
  const messageId = crypto.randomUUID();
  let seq = 0;
  for (const text of params.texts) {
    for (const chunk of chunkThought(text)) {
      await postThought(account, sessionKey, chatId, {
        message_id: messageId,
        agent_id: agentId,
        kind: "text",
        text: chunk,
        seq: seq++,
      });
    }
  }
  if (seq === 0) return;
  // An empty turn: the cloud drops the message and hands the reasoning to this exchange's answer.
  await postTurnStart(account, sessionKey, messageId, chatId);
  await postTurnEnd(account, sessionKey, messageId, chatId);
}

/**
 * Subscribe the relay.
 *
 * `agent_end` is a conversation-access hook, so it needs
 * `plugins.entries.sellerclaw-ui.hooks.allowConversationAccess` in the generated config — the same
 * gate `completion-delivery.ts` already depends on. Fire-and-forget by contract: a failed post is
 * logged and dropped, never awaited by the run.
 */
export function registerReasoningRelay(api: OpenClawPluginApi): void {
  if (typeof api.on !== "function") {
    logWarn(api, "sellerclaw-ui: reasoning relay not installed (api.on unavailable)");
    return;
  }
  api.on("agent_end", (event: AgentEndEvent, ctx?: HookContext): undefined => {
    const sessionKey = asString(ctx?.sessionKey);
    const address = sessionKey ? extractTargetFromSessionKey(sessionKey) : null;
    if (!address) return undefined;
    const runId = asString(event?.runId ?? ctx?.runId);
    // A live chat turn streams its own reasoning through the dispatch; only the runs we cannot
    // pass callbacks into are reported from here.
    if (!isCompletionRun(runId, event?.messages)) return undefined;
    // A run that answered NO_REPLY wrote nothing the owner will read — typically a duplicate
    // completion event waking the supervisor to conclude it has already answered. Its thinking is
    // about the plumbing, and relaying it would put that in the Thought panel next to an answer it
    // had no part in.
    if (isSilentRun(event?.messages)) return undefined;
    const texts = readRunThinking(event?.messages).slice(-MAX_THOUGHT_BLOCKS);
    if (texts.length === 0) return undefined;
    void relayRunThinking({
      api,
      sessionKey,
      chatId: extractChatIdFromAddress(address),
      agentId: asString(ctx?.agentId) || "supervisor",
      texts,
    }).catch((err: unknown) => {
      logError(
        api,
        `sellerclaw-ui: reasoning relay failed run=${runId || "?"} ` +
          `session_key=${sessionKey}: ${String(err)}`,
      );
    });
    return undefined;
  });
  logInfo(api, "sellerclaw-ui: reasoning relay installed");
}
