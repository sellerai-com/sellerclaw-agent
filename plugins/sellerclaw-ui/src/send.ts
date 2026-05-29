export type ScwUiAccount = {
  apiBaseUrl: string;
  userId: string;
  /** Outbound calls to sellerclaw cloud (same value as the agent's AGENT_API_KEY). */
  agentApiKey: string;
  /** Local OpenClaw hooks / sellerclaw-ui inbound: matches sellerclaw-agent ``hooks_token``. */
  internalWebhookSecret: string;
  /** Local sellerclaw-agent base URL inside the container (for media upload proxy). */
  localAgentBaseUrl: string;
};

/** Retries are safe: same `message_id` -> idempotent ingest on the API. */
export const WEBHOOK_MAX_ATTEMPTS = 4;
export const WEBHOOK_BASE_DELAY_MS = 250;

const sendQueues = new Map<string, Promise<unknown>>();

/**
 * Serialize outbound work per session key so parallel sends complete in submission order.
 */
export function enqueueSend<T>(sessionKey: string, fn: () => Promise<T>): Promise<T> {
  const prev = sendQueues.get(sessionKey) ?? Promise.resolve();
  const next = prev.then(fn, fn) as Promise<T>;
  sendQueues.set(sessionKey, next);
  // The .finally() creates a derived promise; suppress its rejection since the
  // caller already handles errors via the returned `next` promise.
  next
    .finally(() => {
      if (sendQueues.get(sessionKey) === next) {
        sendQueues.delete(sessionKey);
      }
    })
    .catch(() => {});
  return next;
}

export function isTransientWebhookStatus(status: number): boolean {
  if (status === 408 || status === 425 || status === 429) {
    return true;
  }
  return status >= 500 && status <= 599;
}

export function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, ms);
  });
}

/** Cloud's "chat is archived, drop this part silently" rejection. */
export const CHAT_ARCHIVED_ERROR_CODE = "chat_archived";

/** Synthetic Response signalling cloud rejected the write because the chat is archived. */
const CHAT_ARCHIVED_RESPONSE: Response = new Response(null, {
  status: 204,
  headers: { "X-Sellerclaw-Drop-Reason": CHAT_ARCHIVED_ERROR_CODE },
});

/** Parse the cloud's ``{error_code, detail}`` envelope; returns empty string if absent. */
function tryReadErrorCode(body: string): string {
  try {
    const parsed = JSON.parse(body) as { error_code?: unknown };
    return typeof parsed.error_code === "string" ? parsed.error_code : "";
  } catch {
    return "";
  }
}

/**
 * POST to the SellerClaw webhook with retries on network errors and transient HTTP statuses.
 * Does not retry 4xx (except 408/425/429): auth and validation errors won't heal on repeat.
 *
 * Exception: HTTP 409 ``chat_archived`` is treated as a terminal **success** — cloud is telling
 * us the chat became archived between the time the agent picked up the user message and the
 * time it tries to write the reply (e.g. the user opened a new chat mid-stream). Throwing would
 * fail the agent's run and surface a useless error; resolving lets the run finish quietly. The
 * reply is dropped, which is the right outcome: archived chats accept no further writes.
 */
export async function postOpenclawWebhook(url: string, init: RequestInit): Promise<Response> {
  let lastError: Error | undefined;
  for (let attempt = 0; attempt < WEBHOOK_MAX_ATTEMPTS; attempt++) {
    if (attempt > 0) {
      await sleep(WEBHOOK_BASE_DELAY_MS * 2 ** (attempt - 1));
    }
    let res: Response;
    try {
      res = await fetch(url, init);
    } catch (err) {
      lastError = err instanceof Error ? err : new Error(String(err));
      continue;
    }
    if (res.ok) {
      return res;
    }
    const status = res.status;
    const detail = await res.text().catch(() => "");
    if (status === 409 && tryReadErrorCode(detail) === CHAT_ARCHIVED_ERROR_CODE) {
      console.info(
        `sellerclaw-ui: webhook ${url} dropped — chat is archived (cloud rejected with chat_archived)`,
      );
      return CHAT_ARCHIVED_RESPONSE;
    }
    lastError = new Error(`sellerclaw-ui: webhook failed (${status}): ${detail.slice(0, 500)}`);
    if (!isTransientWebhookStatus(status)) {
      throw lastError;
    }
  }
  throw lastError ?? new Error("sellerclaw-ui: webhook failed after retries");
}

export function resolveOutboundExtId(p: Record<string, unknown>): string {
  return typeof p.messageId === "string"
    ? p.messageId
    : typeof p.clientMessageId === "string"
      ? p.clientMessageId
      : crypto.randomUUID();
}

/**
 * Upload a local container file path to the sellerclaw-agent's media proxy, which in turn
 * pushes it to cloud File Storage and returns a public HTTPS `download_url`.
 *
 * Bearer auth uses the local `internalWebhookSecret` (= hooks_token); the agent
 * handles the cloud AGENT_API_KEY internally when proxying to the cloud.
 */
export async function uploadLocalMedia(
  account: ScwUiAccount,
  localPath: string,
): Promise<{ downloadUrl: string; filename: string; contentType: string }> {
  const base = account.localAgentBaseUrl.replace(/\/$/, "");
  if (!base) {
    throw new Error("sellerclaw-ui: localAgentBaseUrl is required for media upload");
  }
  const url = `${base}/internal/openclaw/media/upload-local`;
  const res = await postOpenclawWebhook(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${account.internalWebhookSecret}`,
    },
    body: JSON.stringify({ local_path: localPath }),
  });
  const body = (await res.json().catch(() => null)) as
    | { download_url?: string; filename?: string; content_type?: string }
    | null;
  const downloadUrl = body?.download_url;
  if (!downloadUrl || typeof downloadUrl !== "string") {
    throw new Error("sellerclaw-ui: media upload response missing download_url");
  }
  return {
    downloadUrl,
    filename: typeof body?.filename === "string" ? body.filename : "",
    contentType: typeof body?.content_type === "string" ? body.content_type : "",
  };
}

export async function postWebhookMessage(
  account: ScwUiAccount,
  sessionKey: string,
  payload: Record<string, unknown>,
): Promise<{ messageId: string }> {
  const url = `${account.apiBaseUrl.replace(/\/$/, "")}/internal/openclaw/messages`;
  const res = await postOpenclawWebhook(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${account.agentApiKey}`,
    },
    body: JSON.stringify({
      user_id: account.userId,
      session_key: sessionKey,
      ...payload,
    }),
  });
  const body = (await res.json().catch(() => null)) as { message?: { id?: string } } | null;
  const mid = body?.message?.id ?? payload.message_id ?? crypto.randomUUID();
  return { messageId: String(mid) };
}

const IMAGE_URL_EXT_RE = /\.(png|jpe?g|webp|gif)(?:[?#]|$)/i;

/** Classify a resolved media reference as an image or a generic file. */
export function resolveMediaKind(url: string, contentType: string): "image" | "file" {
  return contentType.startsWith("image/") || IMAGE_URL_EXT_RE.test(url) ? "image" : "file";
}

/**
 * Resolve one agent-produced media reference to a publicly deliverable URL.
 *
 * Local container artifacts (e.g. `/home/node/.openclaw/media/...` produced by
 * the `image_generate` tool, or `file://` paths) are proxy-uploaded to cloud
 * File Storage via {@link uploadLocalMedia}; `http(s)` URLs pass through
 * unchanged. `contentType` is empty for pass-through URLs.
 */
export async function resolveOutboundMediaUrl(
  account: ScwUiAccount,
  rawUrl: string,
): Promise<{ url: string; contentType: string }> {
  const trimmed = rawUrl.trim();
  if (!trimmed) {
    throw new Error("sellerclaw-ui: empty media source");
  }
  if (trimmed.startsWith("/") || trimmed.startsWith("file://")) {
    const localPath = trimmed.startsWith("file://")
      ? trimmed.slice("file://".length)
      : trimmed;
    const uploaded = await uploadLocalMedia(account, localPath);
    return { url: uploaded.downloadUrl, contentType: uploaded.contentType };
  }
  return { url: trimmed, contentType: "" };
}

/**
 * Deliver one outbound media item (image or file) as a standalone chat message.
 *
 * Mirrors the `raw_content` shape the `sendImage` channel outbound handler
 * produces, so the SellerClaw web chat renders it identically whether the media
 * came from the `message` tool or from a `MEDIA:` reply directive routed through
 * the inbound `deliver` callback.
 */
export async function postWebhookMediaMessage(
  account: ScwUiAccount,
  sessionKey: string,
  params: {
    mediaUrl: string;
    contentType: string;
    caption: string;
    chatId: string | null;
    messageId: string;
  },
): Promise<{ messageId: string }> {
  const isImage = resolveMediaKind(params.mediaUrl, params.contentType) === "image";
  const rawContent: Record<string, unknown>[] = [];
  if (params.caption.trim()) {
    rawContent.push({ type: "text", text: params.caption });
  }
  rawContent.push(
    isImage
      ? { type: "image_url", image_url: { url: params.mediaUrl } }
      : { type: "file_url", file_url: { url: params.mediaUrl } },
  );
  // Backend requires ``text`` min_length=1. Falling back to ``params.mediaUrl``
  // leaks the cloud URL into the chat bubble (UI renders the text field).
  // A single space passes validation and renders as an empty text section,
  // leaving only the image — the correct outcome for an image-only reply.
  return postWebhookMessage(account, sessionKey, {
    text: params.caption.trim() ? params.caption : " ",
    raw_content: rawContent,
    message_id: params.messageId,
    ...(params.chatId ? { chat_id: params.chatId } : {}),
  });
}

// --- Parts pipeline (unified ordered streaming) -------------------------------------
//
// ``/internal/openclaw/turn`` opens an assistant message; ``…/{id}/part`` appends one
// ordered typed part (text delta / image / file); ``…/{id}/end`` finalizes it. ``chatId``
// is passed as a fallback so the outbound ``sellerclaw-ui:direct:<uuid>`` address (which
// does not match ``openclaw_session_key``) still resolves the chat server-side.

const TURN_PATH = "/internal/openclaw/turn";

export type OutboundPart =
  | { part_id: string; kind: "text"; text: string }
  | {
      part_id: string;
      kind: "image" | "file";
      url: string;
      filename?: string;
      content_type?: string;
    };

async function postTurnRequest(
  account: ScwUiAccount,
  path: string,
  body: Record<string, unknown>,
): Promise<void> {
  const url = `${account.apiBaseUrl.replace(/\/$/, "")}${path}`;
  await postOpenclawWebhook(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${account.agentApiKey}`,
    },
    body: JSON.stringify({ user_id: account.userId, ...body }),
  });
}

export async function postTurnStart(
  account: ScwUiAccount,
  sessionKey: string,
  messageId: string,
  chatId: string | null,
): Promise<void> {
  await postTurnRequest(account, TURN_PATH, {
    session_key: sessionKey,
    message_id: messageId,
    ...(chatId ? { chat_id: chatId } : {}),
  });
}

export async function postTurnPart(
  account: ScwUiAccount,
  sessionKey: string,
  messageId: string,
  part: OutboundPart,
  chatId: string | null,
): Promise<void> {
  await postTurnRequest(account, `${TURN_PATH}/${messageId}/part`, {
    session_key: sessionKey,
    ...(chatId ? { chat_id: chatId } : {}),
    ...part,
  });
}

export async function postTurnEnd(
  account: ScwUiAccount,
  sessionKey: string,
  messageId: string,
  chatId: string | null,
  status: "completed" | "failed" = "completed",
): Promise<void> {
  await postTurnRequest(account, `${TURN_PATH}/${messageId}/end`, {
    session_key: sessionKey,
    status,
    ...(chatId ? { chat_id: chatId } : {}),
  });
}
