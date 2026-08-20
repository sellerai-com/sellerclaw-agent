import type { OpenClawPluginApi } from "openclaw/plugin-sdk/core";

/**
 * Plugin logging that survives the gateway's own logger being unavailable.
 *
 * Two independent things can silence `api.logger`: the plugin may be loaded without one at
 * all, and the gateway's secret redaction (always on since 2026.8) strips individual methods.
 * Optional chaining then swallows the call, so a diagnostic that was written never reaches
 * anyone — which is exactly how the completion-delivery guard came to look like it had never
 * fired in fourteen days of production logs. Falling back to `console.*` keeps the line on
 * the gateway's stdout, which supervisord routes to the container and the cloud ships onward.
 *
 * The bundle also sets `logging.level: "warn"`, so anything worth finding later must be
 * logged at warn or above — `logInfo` is for local debugging, not for production forensics.
 */

/**
 * Marker prefix for every line about answer delivery (completion runs, fallback suppression,
 * rescue sends). Greppable on purpose: `agent_activity.py` matches this exact string to lift
 * these lines into the ping payload, so they reach the cloud even when stdout does not.
 * Changing it means changing that list too.
 */
export const DELIVERY_LOG_PREFIX = "sellerclaw-ui[delivery]";

/**
 * Added to delivery lines that mean the owner may have been left without an answer. Only
 * these are lifted into the ping payload — routine delivery lines would drown the error
 * signal they share it with. `agent_activity.py` matches the prefix and this word together.
 */
export const DELIVERY_UNDELIVERED_MARK = "undelivered";

export function logInfo(api: OpenClawPluginApi, msg: string): void {
  if (api.logger?.info) {
    api.logger.info(msg);
    return;
  }
  console.info(msg);
}

export function logWarn(api: OpenClawPluginApi, msg: string): void {
  if (api.logger?.warn) {
    api.logger.warn(msg);
    return;
  }
  console.warn(msg);
}

export function logError(api: OpenClawPluginApi, msg: string): void {
  if (api.logger?.error) {
    api.logger.error(msg);
    return;
  }
  console.error(msg);
}

/** Warn-level line about answer delivery, tagged so it can be found in shipped logs. */
export function logDelivery(api: OpenClawPluginApi, msg: string): void {
  logWarn(api, `${DELIVERY_LOG_PREFIX} ${msg}`);
}

/**
 * Delivery line for the case that costs the owner an answer. Carries the extra marker the
 * edge agent watches for, so the failure rides along on the next ping instead of waiting to
 * be noticed in a log search nobody runs.
 */
export function logDeliveryFailure(api: OpenClawPluginApi, msg: string): void {
  logWarn(api, `${DELIVERY_LOG_PREFIX} ${DELIVERY_UNDELIVERED_MARK}: ${msg}`);
}
