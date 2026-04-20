import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { enqueueSend, sleep } from "../send.js";

describe("enqueueSend", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("runs two sends for the same session key in order", async () => {
    const order: string[] = [];
    const p1 = enqueueSend("sk", async () => {
      order.push("a");
      await sleep(20);
      order.push("b");
    });
    const p2 = enqueueSend("sk", async () => {
      order.push("c");
    });
    await vi.runAllTimersAsync();
    await Promise.all([p1, p2]);
    expect(order).toEqual(["a", "b", "c"]);
  });

  it("does not block different session keys", async () => {
    const order: string[] = [];
    const p1 = enqueueSend("a", async () => {
      order.push("a1");
      await sleep(30);
      order.push("a2");
    });
    const p2 = enqueueSend("b", async () => {
      order.push("b1");
      await sleep(30);
      order.push("b2");
    });
    await vi.runAllTimersAsync();
    await Promise.all([p1, p2]);
    expect(new Set(order.slice(0, 2))).toEqual(new Set(["a1", "b1"]));
  });

  it("runs the next send after the previous job handled an error internally", async () => {
    vi.useRealTimers();
    const order: string[] = [];
    const p1 = enqueueSend("sk", async () => {
      order.push("first");
      try {
        await Promise.reject(new Error("transient"));
      } catch {
        order.push("recovered");
      }
    });
    const p2 = enqueueSend("sk", async () => {
      order.push("second");
    });
    await p1;
    await p2;
    expect(order).toEqual(["first", "recovered", "second"]);
    vi.useFakeTimers();
  });

  it("allows a new chain on the same key after the previous chain settled", async () => {
    let first = false;
    await enqueueSend("sk", async () => {
      first = true;
    });
    expect(first).toBe(true);
    let second = false;
    await enqueueSend("sk", async () => {
      second = true;
    });
    expect(second).toBe(true);
  });
});
