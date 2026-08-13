import { describe, expect, it, vi } from "vitest";
import { PlaywrightCdpBridge } from "./cdp-bridge";
import type { PlaywrightRelayChrome, RelayTab } from "./relay-executor";

function event() {
  const listeners = new Set<(...args: any[]) => void>();
  return {
    addListener: (listener: (...args: any[]) => void) => listeners.add(listener),
    removeListener: (listener: (...args: any[]) => void) => listeners.delete(listener),
    emit: (...args: any[]) => listeners.forEach((listener) => listener(...args)),
  };
}

function fixture() {
  const tabs = new Map<number, RelayTab>([
    [10, { id: 10, windowId: 7, url: "https://example.com" }],
  ]);
  const debuggerEvent = event();
  const debuggerDetach = event();
  const tabCreated = event();
  const tabRemoved = event();
  const api: PlaywrightRelayChrome = {
    debugger: {
      attach: vi.fn(async () => undefined),
      detach: vi.fn(async () => undefined),
      sendCommand: vi.fn(async (_target, method) =>
        method === "Target.getTargetInfo"
          ? { targetInfo: { targetId: "target-10", type: "page", url: "https://example.com" } }
          : {},
      ),
      onEvent: debuggerEvent,
      onDetach: debuggerDetach,
    },
    tabs: {
      get: vi.fn(async (tabId) => tabs.get(tabId)!),
      create: vi.fn(async (properties) => ({ id: 11, windowId: Number(properties.windowId) })),
      remove: vi.fn(async () => undefined),
      onCreated: tabCreated,
      onRemoved: tabRemoved,
    },
  };
  const events: unknown[] = [];
  const bridge = new PlaywrightCdpBridge(api, 7, (message) => events.push(message));
  bridge.initialize([tabs.get(10)!]);
  return { api, bridge, events, debuggerEvent };
}

describe("Playwright CDP bridge", () => {
  it("provides a browser version without asking the page", async () => {
    const { bridge } = fixture();
    const response = await bridge.handle({ id: 1, method: "Browser.getVersion" });
    expect(response).toMatchObject({
      id: 1,
      result: { protocolVersion: "1.3" },
    });
  });

  it("turns Target.setAutoAttach into approved-tab debugger attachment", async () => {
    const { api, bridge, events } = fixture();
    expect(await bridge.handle({ id: 2, method: "Target.setAutoAttach", params: {} }))
      .toEqual({ id: 2, sessionId: undefined, result: {} });
    expect(api.debugger.attach).toHaveBeenCalledWith({ tabId: 10 }, "1.3");
    expect(events).toContainEqual({
      method: "Target.attachedToTarget",
      params: {
        sessionId: "pw-tab-1",
        targetInfo: {
          targetId: "target-10",
          type: "page",
          url: "https://example.com",
          attached: true,
        },
        waitingForDebugger: false,
      },
    });
  });

  it("routes page and child-session commands without losing session identity", async () => {
    const { api, bridge, debuggerEvent } = fixture();
    await bridge.handle({ id: 3, method: "Target.setAutoAttach", params: {} });
    debuggerEvent.emit(
      { tabId: 10 },
      "Target.attachedToTarget",
      { sessionId: "child-1" },
    );
    await bridge.handle({
      id: 4,
      sessionId: "child-1",
      method: "Runtime.evaluate",
      params: { expression: "1 + 1" },
    });
    expect(api.debugger.sendCommand).toHaveBeenLastCalledWith(
      { tabId: 10, sessionId: "child-1" },
      "Runtime.evaluate",
      { expression: "1 + 1" },
    );
  });

  it("returns protocol errors instead of throwing across the transport", async () => {
    const { bridge } = fixture();
    const response = await bridge.handle({
      id: 5,
      sessionId: "missing",
      method: "Runtime.evaluate",
      params: {},
    });
    expect(response.error?.message).toContain("No tab found for sessionId");
  });
});
