import type { OpenClawPluginApi } from "openclaw/plugin-sdk/core";

import { extractTargetFromSessionKey } from "./channel.js";

/**
 * A completion run reaches the owner only through the ``message`` tool — this hook enforces it.
 *
 * When a subagent finishes, OpenClaw wakes its requester with the child's result and a trigger
 * that reads "A background task completed. Use this result to reply to the user in your normal
 * assistant voice." For a direct-message target that instruction is a trap: the runtime treats
 * a subagent completion aimed at a DM as delivered *only* when the agent called the ``message``
 * tool (``subagentDirectMessageCompletionRequiresMessageTool``), and our chat address —
 * ``sellerclaw-ui:direct:<chat_id>`` — always classifies as direct. Ordinary reply text from
 * that run reaches nobody. Worse, seeing no delivery, the runtime falls back to posting the
 * *child's* own raw completion text into the owner's chat (``deliverTextCompletionDirect``):
 * an internal envelope — ``- **status**: success``, bare UUIDs, written in whatever language
 * the specialist happened to use — presented to the owner as the agent's own update.
 *
 * Seen in production: the supervisor composed a correct, owner-facing Russian summary of an
 * eBay withdrawal, wrote it as ordinary text, and the owner received the eBay specialist's
 * internal report instead. The supervisor's own instructions already carry a strongly worded
 * rule about this and it still did not hold — a prompt alone cannot make the send reliable.
 *
 * So the guard is structural: a completion turn that produced an answer but never sent it gets
 * one more pass, with an instruction naming the tool and the exact target. The runtime bounds
 * this itself (per-run retry budget below, plus its own revision ceiling), so a model that
 * refuses to comply costs a couple of turns, not a loop.
 */

/** Announce (subagent-completion) runs are the only ones this guard applies to. */
const ANNOUNCE_RUN_ID_PREFIX = "announce:";

/**
 * Fallback signal, used when a future runtime stops prefixing announce run ids: the opening
 * line OpenClaw writes for a completion event, in both its plain and protected framings.
 * Matched against the current turn's prompt only — the same text sitting in older history
 * must not turn a normal chat turn into a completion turn.
 */
const COMPLETION_TRIGGER_MARKERS = [
  "A background task completed.",
  "[Internal task completion event]",
];

/**
 * One extra pass per run per instruction. The runtime keys its retry budget on
 * (runId, instruction), so this is a real ceiling, not a hint.
 */
const MAX_RETRY_ATTEMPTS = 2;

interface BeforeAgentFinalizeEvent {
  runId?: unknown;
  sessionKey?: unknown;
  lastAssistantMessage?: unknown;
  messages?: unknown;
}

interface BeforeAgentFinalizeCtx {
  sessionKey?: unknown;
}

interface BeforeAgentFinalizeResult {
  action: "revise";
  retry: { instruction: string; maxAttempts: number; idempotencyKey: string };
}

function asString(value: unknown): string {
  return typeof value === "string" ? value : "";
}

/**
 * Text of the last user-role message, which in a completion run is the trigger the runtime
 * just pushed. Scoped to the *last* one on purpose: the trigger of an earlier completion
 * stays in the transcript, and matching it again would misclassify a live chat turn.
 */
function lastUserMessageText(messages: unknown): string {
  if (!Array.isArray(messages)) return "";
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index] as { role?: unknown; content?: unknown } | null;
    if (!message || typeof message !== "object" || message.role !== "user") continue;
    const content = message.content;
    if (typeof content === "string") return content;
    if (!Array.isArray(content)) return "";
    return content
      .map((block) =>
        block && typeof block === "object" ? asString((block as { text?: unknown }).text) : "",
      )
      .join("\n");
  }
  return "";
}

function isCompletionRun(event: BeforeAgentFinalizeEvent): boolean {
  if (asString(event.runId).startsWith(ANNOUNCE_RUN_ID_PREFIX)) return true;
  // Must *open* the turn, not merely appear in it: an owner who pastes the trigger wording into
  // chat should not have their live turn rerouted through the completion path.
  const trigger = lastUserMessageText(event.messages).trimStart();
  return COMPLETION_TRIGGER_MARKERS.some((marker) => trigger.startsWith(marker));
}

function buildRetryInstruction(target: string): string {
  // The runtime prefixes this with "Do not repeat completed work or rerun tools unless the
  // request explicitly requires it" — so the tool call has to be stated as the requirement.
  //
  // The pass names what to send, not just that to send. Asking only for delivery ("send the
  // answer you just wrote") assumes the text is already the owner's — and the run this guard
  // catches is precisely the one that drifted: a supervisor that had spent its turn closing
  // tasks answered "Task complete … Agent task `dfcff8eb…` is now in `pending_review` status",
  // and the rescue faithfully posted its bookkeeping to the owner, in the wrong language.
  return [
    "This revision requires one tool call and nothing else.",
    "",
    "Your reply text is not delivered in this run. The owner sees only what you send with the",
    "`message` tool — anything else is discarded, and the specialist's raw internal report is",
    "posted in its place.",
    "",
    `Call \`message\` now: action "send", target "${target}".`,
    "",
    "What you send is the owner's answer: what changed in their business and what it means for",
    "them, in the language they write in. Re-read the text you just wrote and rewrite it first if",
    "it does any of these — none of it is an answer:",
    "- opens as a report to yourself (\"Task complete\", \"Here is a summary of what was done\")",
    "- names task machinery: task ids, `pending_review` / `completed`, request-review, subagent",
    "- carries bare UUIDs, or `status:` / `summary:` / `artifacts:` labels from the envelope",
    "- is in a different language from the owner's own messages",
    "",
    "If you already sent that answer with `message` during this turn, reply with exactly NO_REPLY",
    "instead of sending it again.",
  ].join("\n");
}

/**
 * Register the guard. Requires ``plugins.entries.sellerclaw-ui.hooks.allowConversationAccess``
 * in the OpenClaw config: ``before_agent_finalize`` is a conversation hook, and path-loaded
 * (non-bundled) plugins are blocked from those unless the config opts in. Without it OpenClaw
 * logs a warning at load and the hook never runs.
 */
export function registerCompletionDeliveryGuard(api: OpenClawPluginApi): void {
  if (typeof api.on !== "function") return;

  const handler = (
    event: BeforeAgentFinalizeEvent,
    ctx?: BeforeAgentFinalizeCtx,
  ): BeforeAgentFinalizeResult | undefined => {
    const sessionKey = asString(event.sessionKey) || asString(ctx?.sessionKey);
    const target = sessionKey ? extractTargetFromSessionKey(sessionKey) : null;
    // Not a chat of ours (subagent-to-subagent completion, another channel): leave it alone.
    if (!target) return undefined;
    if (!isCompletionRun(event)) return undefined;
    // The runtime only calls this hook when the turn ended with visible assistant text, and on
    // this route visible text is by definition undelivered — so reaching here means an answer
    // was written and nobody will see it.
    if (asString(event.lastAssistantMessage).trim() === "") return undefined;

    api.logger?.warn?.(
      "sellerclaw-ui: completion run answered without the message tool, " +
        `requesting a send pass session_key=${sessionKey}`,
    );

    return {
      action: "revise",
      retry: {
        instruction: buildRetryInstruction(target),
        maxAttempts: MAX_RETRY_ATTEMPTS,
        idempotencyKey: "sellerclaw-ui:completion-requires-message-tool",
      },
    };
  };

  api.on("before_agent_finalize", handler);
}
