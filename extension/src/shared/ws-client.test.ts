import { afterEach, describe, it, expect, vi } from "vitest";
import { BROWSER_WS_PROTOCOL, browserWsProtocols } from "./browser-ws-auth";
import { backoffMs, WsClient } from "./ws-client";

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("reconnect backoff", () => {
  it("starts at 1s and doubles per attempt", () => {
    expect(backoffMs(0)).toBe(1000);
    expect(backoffMs(1)).toBe(2000);
    expect(backoffMs(2)).toBe(4000);
    expect(backoffMs(3)).toBe(8000);
    expect(backoffMs(4)).toBe(16000);
  });

  it("caps at 30s", () => {
    expect(backoffMs(5)).toBe(30000); // 32000 would exceed the cap
    expect(backoffMs(10)).toBe(30000);
    expect(backoffMs(100)).toBe(30000);
  });
});

describe("credential-safe WebSocket handshake", () => {
  it("keeps the scoped credential and browser id out of the URL", () => {
    const calls: unknown[][] = [];
    class FakeWebSocket {
      static OPEN = 1;
      onopen: (() => void) | null = null;
      onmessage: ((event: MessageEvent) => void) | null = null;
      onclose: (() => void) | null = null;
      readyState = 0;
      constructor(...args: unknown[]) {
        calls.push(args);
      }
      close() {}
      send() {}
    }
    vi.stubGlobal("WebSocket", FakeWebSocket);
    const token = "payload.signature";
    const browser = "browser-1";
    const protocols = browserWsProtocols(token, browser);
    new WsClient("wss://app.example/api/v1/browser/ws", protocols).connect();

    expect(calls).toEqual([[
      "wss://app.example/api/v1/browser/ws",
      protocols,
    ]]);
    expect(String(calls[0][0])).not.toContain(token);
    expect(protocols).toEqual([
      BROWSER_WS_PROTOCOL,
      `vibecanvas.browser.auth.${token}`,
      `vibecanvas.browser.id.${browser}`,
    ]);
  });

  it("rejects values that cannot be represented as WebSocket protocol tokens", () => {
    expect(() => browserWsProtocols("token with spaces", "browser-1")).toThrow();
    expect(() => browserWsProtocols("valid.token", "browser:1")).toThrow();
    expect(() => browserWsProtocols("", "browser-1")).toThrow();
  });

  it("requests a fresh capability instead of reconnecting an expired token", () => {
    vi.useFakeTimers();
    class FakeWebSocket {
      static OPEN = 1;
      static instances: FakeWebSocket[] = [];
      onopen: (() => void) | null = null;
      onmessage: ((event: MessageEvent) => void) | null = null;
      onclose: ((event: CloseEvent) => void) | null = null;
      readyState = 0;
      constructor() {
        FakeWebSocket.instances.push(this);
      }
      close() {}
      send() {}
    }
    vi.stubGlobal("WebSocket", FakeWebSocket);
    const client = new WsClient("wss://app.example/api/v1/browser/ws", [
      BROWSER_WS_PROTOCOL,
    ]);
    const refresh = vi.fn();
    client.onAuthRequired(refresh);
    client.connect();
    FakeWebSocket.instances[0].onclose?.({ code: 4401 } as CloseEvent);
    vi.runAllTimers();

    expect(refresh).toHaveBeenCalledOnce();
    expect(FakeWebSocket.instances).toHaveLength(1);
  });
});
