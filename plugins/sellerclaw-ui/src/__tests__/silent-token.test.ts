import { describe, expect, it } from "vitest";

import { isSilentAnswer, visibleAnswerText } from "../silent-token.js";

describe("silent-token", () => {
  it.each([
    ["bare token", "NO_REPLY"],
    ["lower case", "no_reply"],
    ["trailing period", "NO_REPLY."],
    ["bold", "**NO_REPLY**"],
    ["repeated", "NO_REPLY NO_REPLY"],
    ["json string", '"NO_REPLY"'],
    ["json envelope", '{"action":"NO_REPLY"}'],
    ["after a reasoning block", "<thinking>duplicate event</thinking>\nNO_REPLY"],
    ["empty", "   "],
  ])("says nothing for %s", (_label, text) => {
    expect(isSilentAnswer(text)).toBe(true);
    expect(visibleAnswerText(text)).toBe("");
  });

  it("reads prose ending on the token as an ask for silence", () => {
    // Staging chat f0e2835a, 2026-09-04. A duplicate wake-up made the supervisor conclude it had
    // already reported; it said so and closed on the token. The owner was shown the lot, token
    // included. The sentences are addressed to the plumbing, and the token is the ask.
    const text =
      "Всё уже отправлено — отчёт ушёл минуту назад. Ничего добавлять не нужно.\n\nNO_REPLY";

    expect(isSilentAnswer(text)).toBe(true);
    // …and the prose is still recoverable, for the log line that records what silence cost.
    expect(visibleAnswerText(text)).toBe(
      "Всё уже отправлено — отчёт ушёл минуту назад. Ничего добавлять не нужно.",
    );
  });

  it("never leaves a waiting owner with nothing: the token goes, the answer stays", () => {
    // The live-chat reading. Suppressing the whole turn here would answer a question with an empty
    // chat, which is worse than a slightly odd closing line.
    const text = "Оба ремня опубликованы.\n\nNO_REPLY";

    expect(visibleAnswerText(text)).toBe("Оба ремня опубликованы.");
  });

  it("leaves an answer that merely talks about the token alone", () => {
    // The owner asked what the word means, and the agent explained it. Stripping a token that is
    // the subject of the sentence would mangle the answer.
    const text = "NO_REPLY — это служебный ответ агента, владелец его видеть не должен.";

    expect(isSilentAnswer(text)).toBe(false);
    expect(visibleAnswerText(text)).toBe(text);
  });

  it("leaves an ordinary answer untouched", () => {
    const text = "Оба ремня опубликованы на всех трёх магазинах.";

    expect(isSilentAnswer(text)).toBe(false);
    expect(visibleAnswerText(text)).toBe(text);
  });

  it("does not strip a token welded to the end of a word", () => {
    const text = "Флаг называется ANTI-NO_REPLY";

    expect(visibleAnswerText(text)).toBe(text);
  });
});
