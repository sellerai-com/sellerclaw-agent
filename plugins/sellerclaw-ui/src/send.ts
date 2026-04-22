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

/**
 * POST to the SellerClaw webhook with retries on network errors and transient HTTP statuses.
 * Does not retry 4xx (except 408/425/429): auth and validation errors won't heal on repeat.
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
