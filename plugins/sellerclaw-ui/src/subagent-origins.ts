import { chatIdFromOutboundTarget, extractChatIdFromAddress, extractTargetFromSessionKey } from "./channel.js";
import { getSharedState } from "./shared-state.js";

/**
 * Which chat a specialist is working for, so the thinking its run produces can be shown there.
 *
 * A specialist does not run in the owner's chat session. The supervisor spawns it into a session
 * of its own — `agent:marketing:subagent:<uuid>` — which encodes no chat, so `agent_end`, the one
 * hook that carries a run's transcript, arrives with nothing to route by. That is why the owner's
 * Thought panel has only ever shown the supervisor: the specialist's thinking was seen and
 * dropped for want of an address.
 *
 * The runtime knows the connection and states it once, at `subagent_spawned`: the child's session
 * key together with the requester's (`agent:supervisor:sellerclaw-ui:direct:<chat_id>`). This is
 * where that mapping waits until the child's run ends.
 *
 * Kept in memory on purpose: subagents run embedded in the gateway process (`[agent/embedded]` in
 * its log), so the spawn and the end of the run are seen by the same process. The map lives in the
 * shared slot rather than a module-level `Map` because the plugin module is re-evaluated on every
 * plugin-registry pass — and one such pass happens around every embedded agent run, i.e. exactly
 * between the spawn and the end we have to connect (see `shared-state.ts`).
 */
export interface SubagentOrigin {
  /** The chat the work is being done for. */
  chatId: string;
  /** Session key the thought posts are addressed with — always a chat session, never a child's. */
  requesterSessionKey: string;
  /** The specialist doing the thinking. */
  agentId: string;
  /** Who it works for. The Thought panel folds this specialist's blocks under that agent's. */
  parentAgentId: string;
  /** When the spawn was seen, for expiry. */
  recordedAt: number;
}

/**
 * How long a spawn stays connectable to its run's end.
 *
 * A specialist's run is bounded by the runtime's own `runTimeoutSeconds` (an hour at most in this
 * deployment), and a session kept alive after it (`cleanup: "keep"`) may still take follow-up
 * turns from the supervisor. Twelve hours covers a whole working conversation with room to spare;
 * anything still here after that belongs to a chat nobody is reading.
 */
const ORIGIN_TTL_MS = 12 * 60 * 60 * 1000;

/** Bound on remembered spawns, so a long-lived gateway cannot accumulate them without limit. */
const MAX_ORIGINS = 200;

const origins = getSharedState(
  "reasoning-relay:subagent-origins",
  () => new Map<string, SubagentOrigin>(),
);

/** The agent a session belongs to: `agent:<agentId>:…`. Empty for a key of another shape. */
function agentIdFromSessionKey(sessionKey: string): string {
  return sessionKey.match(/^agent:([^:]+):/)?.[1] ?? "";
}

/** Drop what has expired, then the oldest entries until the map is within its bound. */
function prune(now: number): void {
  for (const [key, origin] of origins) {
    if (now - origin.recordedAt > ORIGIN_TTL_MS) origins.delete(key);
  }
  while (origins.size > MAX_ORIGINS) {
    const oldest = origins.keys().next();
    if (oldest.done) break;
    origins.delete(oldest.value);
  }
}

/**
 * Remember a spawn, if it can be traced back to a chat of ours.
 *
 * Returns the stored origin, or `null` for a spawn that has nothing to do with the owner's chat —
 * a cron run, a task run, another channel. Those are the majority on a busy gateway and are meant
 * to be forgotten immediately rather than followed.
 */
export function rememberSubagentOrigin(params: {
  childSessionKey: string;
  agentId: string;
  requesterSessionKey: string;
  /** `requester.to` from the spawn event — the same address in its outbound spelling. */
  requesterTarget?: string | null;
  now?: number;
}): SubagentOrigin | null {
  const { childSessionKey, requesterSessionKey } = params;
  if (!childSessionKey || !requesterSessionKey) return null;
  const now = params.now ?? Date.now();
  prune(now);

  const agentId = params.agentId || agentIdFromSessionKey(childSessionKey);
  if (!agentId) return null;

  const address = extractTargetFromSessionKey(requesterSessionKey);
  const chatId =
    (address ? extractChatIdFromAddress(address) : null) ??
    (params.requesterTarget ? chatIdFromOutboundTarget(params.requesterTarget) : null);

  if (chatId) {
    const origin: SubagentOrigin = {
      chatId,
      requesterSessionKey,
      agentId,
      parentAgentId: agentIdFromSessionKey(requesterSessionKey) || "supervisor",
      recordedAt: now,
    };
    origins.set(childSessionKey, origin);
    return origin;
  }

  // A specialist that delegates further: its own session encodes no chat either, but we are
  // already following it, so the grandchild inherits the chat — and is folded under the
  // specialist that asked for it, not under the supervisor.
  const parent = lookupSubagentOrigin(requesterSessionKey, now);
  if (!parent) return null;
  const origin: SubagentOrigin = {
    chatId: parent.chatId,
    requesterSessionKey: parent.requesterSessionKey,
    agentId,
    parentAgentId: parent.agentId,
    recordedAt: now,
  };
  origins.set(childSessionKey, origin);
  return origin;
}

/** The chat a specialist session works for, or `null` if this is not a run we follow. */
export function lookupSubagentOrigin(
  sessionKey: string,
  now: number = Date.now(),
): SubagentOrigin | null {
  if (!sessionKey) return null;
  const origin = origins.get(sessionKey);
  if (!origin) return null;
  if (now - origin.recordedAt > ORIGIN_TTL_MS) {
    origins.delete(sessionKey);
    return null;
  }
  return origin;
}

/** Test-only: forget every spawn so cases cannot leak into each other. */
export function __resetSubagentOrigins(): void {
  origins.clear();
}
