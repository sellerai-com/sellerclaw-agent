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
