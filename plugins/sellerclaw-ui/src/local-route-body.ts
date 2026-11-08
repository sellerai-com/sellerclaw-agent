import type { IncomingMessage, ServerResponse } from "node:http";

import { readJsonWebhookBodyOrReject } from "openclaw/plugin-sdk/webhook-ingress";

/**
 * Shared plumbing of the local, gateway-authenticated routes the agent server drives (cron, steer,
 * session questions): read a JSON object body, answer with JSON.
 */

export function reply(res: ServerResponse, status: number, body: unknown): true {
  res.statusCode = status;
  res.end(JSON.stringify(body));
  return true;
}

/** The request's JSON object body, or `null` once the request has already been answered. */
export async function readObjectBody(
  req: IncomingMessage,
  res: ServerResponse,
): Promise<Record<string, unknown> | null> {
  const readResult = await readJsonWebhookBodyOrReject({ req, res });
  if (
    !readResult ||
    typeof readResult !== "object" ||
    !("ok" in readResult) ||
    !(readResult as { ok: boolean }).ok
  ) {
    return null; // already answered by the reader
  }
  const body = (readResult as { ok: true; value: unknown }).value;
  if (!body || typeof body !== "object" || Array.isArray(body)) {
    reply(res, 400, { error: "Invalid JSON body" });
    return null;
  }
  return body as Record<string, unknown>;
}

/** A non-empty trimmed string field, or `""`. */
export function stringField(body: Record<string, unknown>, key: string): string {
  const value = body[key];
  return typeof value === "string" ? value.trim() : "";
}
