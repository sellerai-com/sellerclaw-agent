import type { OpenClawPluginApi } from "openclaw/plugin-sdk/core";

import {
  extractChatIdFromAddress,
  extractTargetFromSessionKey,
  resolveSellerclawUiAccount,
} from "./channel.js";
import { isCompletionRun, isSilentRun, lastUserMessageIndex } from "./completion-run.js";
import { logError, logInfo, logWarn } from "./log.js";
import { postThought, postTurnEnd, postTurnStart } from "./send.js";
import { getSharedState } from "./shared-state.js";
import { lookupSubagentOrigin, rememberSubagentOrigin } from "./subagent-origins.js";

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
 *
 * The same hook carries the other half of a delegated answer: the **specialist's** own thinking.
 * Its run happens in a session that encodes no chat, so it used to be dropped here for want of an
 * address; `subagent_spawned` supplies that address (see `subagent-origins.ts`), and the blocks go
 * out marked with the specialist as their author and the agent that delegated as their parent —
 * which is what folds them into one named block in the owner's panel instead of scattering them
 * among the supervisor's own thoughts.
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

/**
 * What a run has already reported, so a second `agent_end` for it cannot say it twice.
 *
 * The hook fires per finalized *attempt*, not per run: a turn that is retried, or sent back for
 * one more model pass, ends more than once, and each time the transcript holds everything since
 * the trigger — including the thinking already relayed. Without this the owner reads the same
 * paragraph two and three times over. Keyed by run: two different runs saying the same sentence
 * are two real thoughts, and both belong in the panel.
 */
const RELAYED_TTL_MS = 2 * 60 * 60 * 1000;
const MAX_RELAYED_RUNS = 100;

/**
 * Blocks remembered per run. A run reports at most `MAX_THOUGHT_BLOCKS` per attempt, so this
 * covers a heavily retried one and still cannot grow with the number of attempts.
 */
const MAX_RELAYED_TEXTS_PER_RUN = 500;

const relayedByRun = getSharedState(
  "reasoning-relay:relayed-by-run",
  () => new Map<string, { texts: Set<string>; at: number }>(),
);

/** The blocks of this run not reported yet, remembering them as reported. */
function unreportedTexts(runKey: string, texts: string[], now: number = Date.now()): string[] {
  if (!runKey) return texts;
  for (const [key, entry] of relayedByRun) {
    if (now - entry.at > RELAYED_TTL_MS) relayedByRun.delete(key);
  }
  while (relayedByRun.size > MAX_RELAYED_RUNS) {
    const oldest = relayedByRun.keys().next();
    if (oldest.done) break;
    relayedByRun.delete(oldest.value);
  }
  const entry = relayedByRun.get(runKey) ?? { texts: new Set<string>(), at: now };
  const fresh = texts.filter((text) => !entry.texts.has(text));
  for (const text of fresh) entry.texts.add(text);
  while (entry.texts.size > MAX_RELAYED_TEXTS_PER_RUN) {
    const oldest = entry.texts.values().next();
    if (oldest.done) break;
    entry.texts.delete(oldest.value);
  }
  entry.at = now;
  // Re-insert so the eviction above drops the runs that went quiet, not the busiest one.
  relayedByRun.delete(runKey);
  relayedByRun.set(runKey, entry);
  return fresh;
}

/** Test-only: forget what has been reported. */
export function __resetRelayedThinking(): void {
  relayedByRun.clear();
}

interface AgentEndEvent {
  runId?: unknown;
  messages?: unknown;
}

interface HookContext {
  sessionKey?: unknown;
  runId?: unknown;
  agentId?: unknown;
  /** `subagent_spawned` only: the session the specialist was given. */
  childSessionKey?: unknown;
  /** `subagent_spawned` only: the session that asked for the work. */
  requesterSessionKey?: unknown;
}

/** `subagent_spawned`: the runtime's announcement that a specialist has been given a session. */
interface SubagentSpawnedEvent {
  childSessionKey?: unknown;
  /** The specialist the work went to. */
  agentId?: unknown;
  requester?: { to?: unknown } | null;
}

/** Post one run's thinking and close the empty turn that carries it. */
async function relayRunThinking(params: {
  api: OpenClawPluginApi;
  sessionKey: string;
  chatId: string | null;
  agentId: string;
  /** Set for a specialist's run: the agent it works for, which the panel groups it under. */
  parentAgentId?: string | null;
  texts: string[];
}): Promise<void> {
  const { api, sessionKey, chatId, agentId, parentAgentId } = params;
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
        ...(parentAgentId ? { parent_agent_id: parentAgentId } : {}),
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
  // Spawns are what connect a specialist's session to the chat it works for. Cheap and silent:
  // a spawn with no chat behind it (cron, task runs, another channel) is dropped on the spot.
  api.on("subagent_spawned", (event: SubagentSpawnedEvent, ctx?: HookContext): undefined => {
    const childSessionKey = asString(event?.childSessionKey ?? ctx?.childSessionKey);
    const requesterSessionKey = asString(ctx?.requesterSessionKey);
    const origin = rememberSubagentOrigin({
      childSessionKey,
      agentId: asString(event?.agentId),
      requesterSessionKey,
      requesterTarget: asString(event?.requester?.to) || null,
    });
    if (origin) {
      logInfo(
        api,
        `sellerclaw-ui: following ${origin.agentId} for chat ${origin.chatId} ` +
          `(session ${childSessionKey})`,
      );
    }
    return undefined;
  });
  api.on("agent_end", (event: AgentEndEvent, ctx?: HookContext): undefined => {
    const sessionKey = asString(ctx?.sessionKey);
    const address = sessionKey ? extractTargetFromSessionKey(sessionKey) : null;
    // A run in the chat's own session, or a specialist's run we know the chat for. Anything else
    // (cron, task runs, other channels) has no place in the owner's Thought panel.
    const origin = address ? null : lookupSubagentOrigin(sessionKey);
    if (!address && !origin) return undefined;
    const runId = asString(event?.runId ?? ctx?.runId);
    // A live chat turn streams its own reasoning through the dispatch; only the runs we cannot
    // pass callbacks into are reported from here. A specialist's run is never one of ours — no
    // callback reaches it, so every one of its turns is reported.
    if (address && !isCompletionRun(runId, event?.messages)) return undefined;
    // A run that answered NO_REPLY wrote nothing the owner will read — typically a duplicate
    // completion event waking the supervisor to conclude it has already answered. Its thinking is
    // about the plumbing, and relaying it would put that in the Thought panel next to an answer it
    // had no part in.
    if (isSilentRun(event?.messages)) return undefined;
    const texts = unreportedTexts(
      runId ? `${sessionKey}\u0000${runId}` : "",
      readRunThinking(event?.messages).slice(-MAX_THOUGHT_BLOCKS),
    );
    if (texts.length === 0) return undefined;
    void relayRunThinking({
      api,
      // Thoughts are addressed with the chat's session either way: a child's session key encodes
      // no chat, and the cloud routes reasoning by the address it is given.
      sessionKey: origin ? origin.requesterSessionKey : sessionKey,
      chatId: origin ? origin.chatId : extractChatIdFromAddress(address as string),
      agentId: asString(ctx?.agentId) || origin?.agentId || "supervisor",
      parentAgentId: origin?.parentAgentId ?? null,
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
