/**
 * Fills the gateway context resolver missing from OpenClaw's plugin HTTP route scope.
 *
 * OpenClaw builds a per-request scope for plugin HTTP routes (`createPluginRouteRuntimeScope`)
 * that carries the live gateway `context` but no `resolveGatewayContext` callback. The embedded
 * agent runner (`executeAgentTurnInternal`) reads only the callback:
 *
 *   readChannelContextGatewayContextResolver(sessionCtx) ?? scope?.resolveGatewayContext
 *
 * so a turn dispatched from one of our routes is admitted with **no gateway binding**. Every
 * subagent it spawns then falls back to `fenceScheduledGatewayContextResolver`, which mints a
 * *fresh closure per child*; when the wave settles, `getSharedGatewayContextResolver` compares
 * those closures by object identity, finds them different, and hands the completion dispatch a
 * `() => undefined` stub. The stub also suppresses the ambient-scope fallback inside
 * `getInProcessGatewayRequestContext`, so waking the chat fails with
 * "In-process gateway dispatch requires a gateway request scope or instance binding".
 *
 * Net effect without this shim: any chat turn that starts TWO OR MORE specialists never wakes the
 * chat through the normal path. One specialist works (nothing to compare against). Measured on
 * 2026.8.2; `getInProcessGatewayRequestContext` already accepts `scope.context` as a valid
 * fallback, so filling the callback from the same object is exactly what upstream does elsewhere.
 *
 * The scope object is created per request, so mutating it is request-local and cannot leak.
 * Remove this once the upstream fix ships (openclaw/openclaw#136487).
 */
import { getPluginRuntimeGatewayRequestScope } from "openclaw/plugin-sdk/plugin-runtime";

/**
 * Runtime scope surface for the routes that dispatch a supervisor turn (chat inbound, scheduled
 * run), so the turn's session tools can read other sessions.
 *
 * The default `"write-default"` surface hands the turn a single `operator.write`. That is meant to
 * satisfy `operator.read` too, but in-process tool dispatch narrows each call's scopes with a literal
 * `includes` against the route's set, so `sessions_history` / `sessions_list` / `sessions_search`
 * (`operator.read` methods) fail with "missing scope: operator.read" in a turn started here and in
 * the continuation that resumes it after `sessions_yield`. Cron runs never pass through a route and
 * are unaffected. Seen on 2026.9.5 locally, on staging and on production.
 *
 * `"trusted-operator"` gives the turn the full operator set instead (shared-secret auth ignores
 * `x-openclaw-scopes`, so it cannot be narrowed to read+write). That matches OpenClaw's trust model
 * for this deployment: one Gateway per owner, the caller holds the shared gateway token, and the
 * agent already runs host commands with no approval step.
 *
 * Remove this (back to the default surface) once the pinned OpenClaw includes the upstream fix —
 * openclaw/openclaw#141799, fixed by #141865 (`9d15063`, merged 2026-09-20, after 2026.9.5).
 */
export const SUPERVISOR_TURN_SCOPE_SURFACE = "trusted-operator";

/**
 * Call at the top of every plugin HTTP route handler that dispatches an agent turn, before the
 * dispatch. Returns true when the resolver was missing and has been filled.
 */
export function ensureRouteScopeGatewayResolver(): boolean {
  const scope = getPluginRuntimeGatewayRequestScope();
  if (!scope || scope.resolveGatewayContext || !scope.context) return false;
  const context = scope.context;
  scope.resolveGatewayContext = context.resolveGatewayContext ?? ((): unknown => context);
  return true;
}
