import { describe, expect, it } from "vitest";

import { isTransportTurnFailure } from "../run-outcome.js";

/**
 * Which failures a turn may re-ask on its own.
 *
 * The line is "the pipe broke" vs "the request was refused": the first is worth one more try
 * (the work already done is in the session), the second would cost the owner money to reproduce
 * the same message. Cases are the real strings seen in production logs.
 */
describe("isTransportTurnFailure", () => {
  it.each([
    ["LLM request timed out.", "read timeout"],
    ["LLM request timed out. rawError=terminated", "stream cut mid-answer"],
    [
      "LLM request failed. rawError=litellm.APIConnectionError: OpenAIException - Response payload is not completed: <TransferEncodingError: 400>. SSLError(1, '[SSL] record layer failure')",
      "TLS dropped mid-stream",
    ],
    ["LLM request failed: network connection error. rawError=Connection error.", "network error"],
    ["LLM request failed. rawError=socket hang up", "socket hang up"],
    [
      "LLM request failed. rawError=502 Traffic made it to the agent, but the upstream refused.",
      "bad gateway",
    ],
  ])("retries %s (%s)", (text) => {
    expect(isTransportTurnFailure(text)).toBe(true);
  });

  it.each([
    ["LLM request rate limited.", "quota, not connectivity"],
    ["Your account has insufficient balance to continue.", "billing needs a human"],
    ["LLM request failed: authentication failed for provider litellm", "bad credentials"],
    [
      "Context overflow: prompt too large for the model. Try /reset to start a fresh session.",
      "retry reproduces it",
    ],
    [
      "The selected model was not found by the provider. Check the model id.",
      "config, not transport",
    ],
    ["", "no evidence at all"],
    ["   ", "blank message"],
    ["Something went wrong.", "unrecognised wording is not assumed safe"],
  ])("does not retry %s (%s)", (text) => {
    expect(isTransportTurnFailure(text)).toBe(false);
  });

  it("keeps a rate limit delivered over a failing gateway on the no-retry side", () => {
    // Both families named in one message: the request would be refused again, so the
    // human-needed side wins regardless of the transport wording.
    expect(isTransportTurnFailure("503 Service Unavailable: rate limit exceeded")).toBe(false);
  });
});
