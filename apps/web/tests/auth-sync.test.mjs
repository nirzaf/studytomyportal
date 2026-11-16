import { afterEach, describe, expect, test } from "bun:test";

import {
  AUTH_BROADCAST_CHANNEL,
  AUTH_STORAGE_SYNC_KEY,
  createAuthSyncReceiver,
  publishAuthSync,
} from "../lib/auth/authSync.ts";

const originalWindow = Object.getOwnPropertyDescriptor(globalThis, "window");

afterEach(() => {
  if (originalWindow) {
    Object.defineProperty(globalThis, "window", originalWindow);
  } else {
    delete globalThis.window;
  }
});

describe("auth synchronization messages", () => {
  test("publishes the same credential-free event over both transports", () => {
    const writes = [];
    const channelMessages = [];
    Object.defineProperty(globalThis, "window", {
      configurable: true,
      value: {
        localStorage: {
          setItem: (key, value) => writes.push([key, value]),
        },
      },
    });

    publishAuthSync("LOGIN", {
      postMessage: (message) => channelMessages.push(message),
    });

    expect(AUTH_BROADCAST_CHANNEL).toBe("learnhouse_auth_sync");
    expect(channelMessages).toHaveLength(1);
    expect(writes).toHaveLength(1);
    expect(writes[0][0]).toBe(AUTH_STORAGE_SYNC_KEY);
    expect(JSON.parse(writes[0][1])).toEqual(channelMessages[0]);
    expect(Object.keys(channelMessages[0]).sort()).toEqual(["at", "nonce", "type"]);
  });

  test("falls back to storage when the broadcast channel has closed", () => {
    const writes = [];
    Object.defineProperty(globalThis, "window", {
      configurable: true,
      value: { localStorage: { setItem: (key, value) => writes.push([key, value]) } },
    });

    expect(() => publishAuthSync("LOGOUT", {
      postMessage: () => { throw new Error("channel closed"); },
    })).not.toThrow();
    expect(writes).toHaveLength(1);
    expect(JSON.parse(writes[0][1]).type).toBe("LOGOUT");
  });

  test("still broadcasts when local storage is unavailable", () => {
    const channelMessages = [];
    Object.defineProperty(globalThis, "window", {
      configurable: true,
      value: { localStorage: { setItem: () => { throw new Error("storage blocked"); } } },
    });

    expect(() => publishAuthSync("LOGIN", {
      postMessage: (message) => channelMessages.push(message),
    })).not.toThrow();
    expect(channelMessages).toHaveLength(1);
  });

  test("processes a nonce-bearing event once across duplicate transports", () => {
    const events = [];
    const receive = createAuthSyncReceiver((type) => events.push(type), () => 5000);
    const message = { type: "LOGIN", nonce: "shared-event" };

    receive(message);
    receive(JSON.parse(JSON.stringify(message)));
    receive({ type: "LOGOUT", nonce: "next-event" });

    expect(events).toEqual(["LOGIN", "LOGOUT"]);
  });

  test("accepts later legacy login events but suppresses a near duplicate", () => {
    const events = [];
    let now = 5000;
    const receive = createAuthSyncReceiver((type) => events.push(type), () => now);

    receive("LOGIN");
    now += 500;
    receive({ type: "LOGIN" });
    now += 501;
    receive("LOGIN");

    expect(events).toEqual(["LOGIN", "LOGIN"]);
  });

  test("ignores malformed event types", () => {
    const events = [];
    const receive = createAuthSyncReceiver((type) => events.push(type), () => 5000);

    receive(null);
    receive({ type: "REFRESH", nonce: "ignored" });
    receive({ type: 1 });

    expect(events).toEqual([]);
  });
});
