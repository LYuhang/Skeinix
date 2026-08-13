import { describe, expect, it, vi } from "vitest";
import {
  PLAYWRIGHT_RELAY_ALLOWED_COMMANDS,
  PlaywrightRelayExecutor,
  type PlaywrightRelayChrome,
  type RelayTab,
} from "./relay-executor";

type Listener<T extends (...args: never[]) => void> = T;

function event<T extends (...args: never[]) => void>() {
  const listeners = new Set<Listener<T>>();
  return {
    addListener: (listener: T) => listeners.add(listener),
    removeListener: (listener: T) => listeners.delete(listener),
    emit: (...args: Parameters<T>) => {
      for (const listener of listeners) listener(...args);
    },
    count: () => listeners.size,
  };
}

function fixture(onOwnedTabDetached = vi.fn(), onAttachedTabsChanged = vi.fn()) {
  const tabs = new Map<number, RelayTab>([
    [10, { id: 10, windowId: 7, title: "Allowed" }],
    [11, { id: 11, windowId: 8, title: "Other window" }],
  ]);
  const debuggerEvent = event<any>();
  const debuggerDetach = event<any>();
  const tabCreated = event<any>();
  const tabRemoved = event<any>();
  const api: PlaywrightRelayChrome = {
    debugger: {
      attach: vi.fn(async () => undefined),
      detach: vi.fn(async () => undefined),
      sendCommand: vi.fn(async () => ({ value: "ok" })),
      onEvent: debuggerEvent,
      onDetach: debuggerDetach,
    },
    tabs: {
      get: vi.fn(async (tabId) => {
        const tab = tabs.get(tabId);
        if (!tab) throw new Error("missing tab");
        return tab;
      }),
      create: vi.fn(async (properties) => {
        const tab = { id: 12, windowId: Number(properties.windowId) };
        tabs.set(12, tab);
        return tab;
      }),
      remove: vi.fn(async () => undefined),
      onCreated: tabCreated,
      onRemoved: tabRemoved,
    },
  };
  const messages: unknown[] = [];
  const relay = new PlaywrightRelayExecutor(
    api,
    7,
    (message) => messages.push(message),
    onOwnedTabDetached,
    onAttachedTabsChanged,
  );
  return {
    api,
    tabs,
    relay,
    messages,
    debuggerEvent,
    debuggerDetach,
    tabCreated,
    tabRemoved,
    onOwnedTabDetached,
    onAttachedTabsChanged,
  };
}

describe("Playwright extension relay", () => {
  it("keeps the upstream command surface deliberately small", () => {
    expect([...PLAYWRIGHT_RELAY_ALLOWED_COMMANDS].sort()).toEqual([
      "chrome.debugger.attach",
      "chrome.debugger.detach",
      "chrome.debugger.sendCommand",
      "chrome.tabs.create",
      "chrome.tabs.remove",
    ]);
  });

  it("advertises only selected tabs in the side-panel window", () => {
    const { relay, messages } = fixture();
    relay.initialize([
      { id: 10, windowId: 7 },
      { id: 11, windowId: 8 },
    ]);
    expect(messages).toEqual([
      { method: "chrome.tabs.onCreated", params: [{ id: 10, windowId: 7 }] },
      { method: "extension.initialized", params: [] },
    ]);
  });

  it("attaches and forwards CDP only for an allowed tab", async () => {
    const { relay, api } = fixture();
    expect(await relay.handle({
      id: 1,
      method: "chrome.debugger.attach",
      params: [{ tabId: 10 }, "1.3"],
    })).toEqual({ id: 1, result: {} });
    expect(relay.attachedTabIds()).toEqual([10]);

    expect(await relay.handle({
      id: 2,
      method: "chrome.debugger.sendCommand",
      params: [{ tabId: 10 }, "Runtime.evaluate", { expression: "1 + 1" }],
    })).toEqual({ id: 2, result: { value: "ok" } });
    expect(api.debugger.sendCommand).toHaveBeenCalledWith(
      { tabId: 10 },
      "Runtime.evaluate",
      { expression: "1 + 1" },
    );

    await relay.handle({
      id: 9,
      method: "chrome.debugger.sendCommand",
      params: [
        { tabId: 10, sessionId: "child-session" },
        "Runtime.evaluate",
        { expression: "2 + 2" },
      ],
    });
    expect(api.debugger.sendCommand).toHaveBeenLastCalledWith(
      { tabId: 10, sessionId: "child-session" },
      "Runtime.evaluate",
      { expression: "2 + 2" },
    );
  });

  it("rejects tabs outside the side-panel window and uncontrolled tabs", async () => {
    const { relay, api } = fixture();
    const outside = await relay.handle({
      id: 3,
      method: "chrome.debugger.attach",
      params: [{ tabId: 11 }, "1.3"],
    });
    expect(outside.error?.message).toContain("outside the side-panel window");
    expect(api.debugger.attach).not.toHaveBeenCalled();

    const uncontrolled = await relay.handle({
      id: 4,
      method: "chrome.debugger.sendCommand",
      params: [{ tabId: 10 }, "Runtime.evaluate", {}],
    });
    expect(uncontrolled.error?.message).toContain("uncontrolled tab");
  });

  it("forwards events only for attached tabs and same-window popups", async () => {
    const { relay, messages, debuggerEvent, tabCreated, tabRemoved } = fixture();
    await relay.handle({
      id: 5,
      method: "chrome.debugger.attach",
      params: [{ tabId: 10 }, "1.3"],
    });
    debuggerEvent.emit({ tabId: 11 }, "Runtime.consoleAPICalled", {});
    debuggerEvent.emit({ tabId: 10 }, "Runtime.consoleAPICalled", { type: "log" });
    tabCreated.emit({ id: 12, windowId: 7, openerTabId: 10 });
    tabCreated.emit({ id: 13, windowId: 8, openerTabId: 10 });
    tabRemoved.emit(11);
    tabRemoved.emit(10);

    expect(messages).toEqual([
      {
        method: "chrome.debugger.onEvent",
        params: [{ tabId: 10 }, "Runtime.consoleAPICalled", { type: "log" }],
      },
      {
        method: "chrome.tabs.onCreated",
        params: [{ id: 12, windowId: 7, openerTabId: 10 }],
      },
      { method: "chrome.tabs.onRemoved", params: [10] },
    ]);
  });

  it("reports an unexpected debugger detach to the session control plane", async () => {
    const {
      relay,
      debuggerDetach,
      onOwnedTabDetached,
      onAttachedTabsChanged,
    } = fixture();
    await relay.handle({
      id: 51,
      method: "chrome.debugger.attach",
      params: [{ tabId: 10 }, "1.3"],
    });
    debuggerDetach.emit({ tabId: 10 }, "canceled_by_user");

    expect(onOwnedTabDetached).toHaveBeenCalledWith(10, "canceled_by_user");
    expect(onAttachedTabsChanged).toHaveBeenLastCalledWith(
      [],
      "detached",
      10,
    );
    expect(relay.attachedTabIds()).toEqual([]);
  });

  it("projects attached and removed tab ownership to durable session state", async () => {
    const { relay, tabRemoved, onAttachedTabsChanged } = fixture();
    await relay.handle({
      id: 52,
      method: "chrome.debugger.attach",
      params: [{ tabId: 10 }, "1.3"],
    });
    expect(onAttachedTabsChanged).toHaveBeenLastCalledWith(
      [10],
      "attached",
      10,
    );

    tabRemoved.emit(10);
    expect(onAttachedTabsChanged).toHaveBeenLastCalledWith(
      [],
      "tab_removed",
      10,
    );
  });

  it("forces new tabs into the side-panel window", async () => {
    const { relay, api } = fixture();
    expect(await relay.handle({
      id: 6,
      method: "chrome.tabs.create",
      params: [{ url: "https://example.com", windowId: 99 }],
    })).toEqual({ id: 6, result: { id: 12, windowId: 7 } });
    expect(api.tabs.create).toHaveBeenCalledWith({
      url: "https://example.com",
      windowId: 7,
    });
  });

  it("rejects arbitrary chrome methods", async () => {
    const { relay } = fixture();
    const result = await relay.handle({
      id: 7,
      method: "chrome.history.search",
      params: [],
    });
    expect(result.error).toEqual({
      code: -32600,
      message: "Unsupported Playwright relay method: chrome.history.search",
    });
  });

  it("detaches controlled tabs and removes listeners on close", async () => {
    const { relay, api, debuggerEvent, tabCreated } = fixture();
    await relay.handle({
      id: 8,
      method: "chrome.debugger.attach",
      params: [{ tabId: 10 }, "1.3"],
    });
    await relay.close();
    expect(api.debugger.detach).toHaveBeenCalledWith({ tabId: 10 });
    expect(debuggerEvent.count()).toBe(0);
    expect(tabCreated.count()).toBe(0);
  });
});
