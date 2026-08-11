import { describe, it, expect, vi } from "vitest";
import { SessionManager, type Debugger } from "./session-manager";

function fakeDebugger(): Debugger & {
  fire: (m: string, p: any, sourceTabId?: number) => void;
} {
  const listeners: Array<(
    source: { tabId?: number },
    m: string,
    p: any,
  ) => void> = [];
  return {
    attach: vi.fn().mockResolvedValue(undefined),
    detach: vi.fn().mockResolvedValue(undefined),
    sendCommand: vi.fn().mockImplementation(async (_t, method) => {
      if (method === "Target.getTargetInfo")
        return { targetInfo: { targetId: "T0", url: "about:blank" } };
      return {};
    }),
    onEvent: (cb) => listeners.push(cb),
    getTargets: vi.fn().mockResolvedValue([]),
    fire: (m, p, sourceTabId = 42) =>
      listeners.forEach((l) => l({ tabId: sourceTabId }, m, p)),
  };
}

describe("SessionManager", () => {
  it("attaches the root tab and returns a target_id", async () => {
    const dbg = fakeDebugger();
    const sm = new SessionManager(dbg);
    const tid = await sm.attachRoot(42);
    expect(dbg.attach).toHaveBeenCalledWith(42);
    expect(sm.tabIdFor(tid)).toBe(42);
  });

  it("adopts an extension-owned debugger attachment after proving command access", async () => {
    const dbg = fakeDebugger();
    (dbg.attach as ReturnType<typeof vi.fn>).mockRejectedValueOnce(
      new Error("Another debugger is already attached to the tab"),
    );

    const sm = new SessionManager(dbg);
    const tid = await sm.attachRoot(42);

    expect(tid).toBe("T0");
    expect(sm.tabIdFor(tid)).toBe(42);
    expect(dbg.sendCommand).toHaveBeenCalledWith(
      { tabId: 42 },
      "Target.setAutoAttach",
      expect.objectContaining({ autoAttach: true, flatten: true }),
    );
  });

  it("does not adopt a tab attached by some other debugger", async () => {
    const dbg = fakeDebugger();
    const conflict = new Error("Another debugger is already attached to the tab");
    (dbg.attach as ReturnType<typeof vi.fn>).mockRejectedValueOnce(conflict);
    (dbg.sendCommand as ReturnType<typeof vi.fn>).mockRejectedValueOnce(
      new Error("Cannot access an external debugger attachment"),
    );

    const sm = new SessionManager(dbg);
    await expect(sm.attachRoot(42)).rejects.toBe(conflict);
    expect(sm.knownTargets()).toEqual([]);
  });

  it("reports a healthy current extension attachment", async () => {
    const dbg = fakeDebugger();
    const sm = new SessionManager(dbg);
    await sm.attachRoot(42);
    (dbg.getTargets as ReturnType<typeof vi.fn>).mockResolvedValueOnce([
      { id: "T0", tabId: 42, type: "page", attached: true },
    ]);

    const health = await sm.health();

    expect(health).toMatchObject({
      state: "healthy",
      controlled_tab_count: 1,
      stale_attachment_count: 0,
      conflict_count: 0,
      owner_match: true,
      recommended_action: "continue",
    });
  });

  it("reports a proven extension-owned ghost as safely recoverable", async () => {
    const dbg = fakeDebugger();
    (dbg.getTargets as ReturnType<typeof vi.fn>).mockResolvedValueOnce([
      { id: "T-stale", tabId: 42, type: "page", attached: true },
    ]);
    const sm = new SessionManager(dbg);

    const health = await sm.health();

    expect(health).toMatchObject({
      state: "stale_extension_attachment",
      controlled_tab_count: 0,
      extension_owned_attachment_count: 1,
      stale_attachment_count: 1,
      conflict_count: 0,
      safe_to_cleanup: true,
      recommended_action: "rediscover_tabs_and_start_selected_session",
    });
  });

  it("reports an unknown debugger owner without claiming cleanup is safe", async () => {
    const dbg = fakeDebugger();
    (dbg.getTargets as ReturnType<typeof vi.fn>).mockResolvedValueOnce([
      { id: "T-external", tabId: 42, type: "page", attached: true },
    ]);
    (dbg.sendCommand as ReturnType<typeof vi.fn>).mockRejectedValueOnce(
      new Error("Cannot access target"),
    );
    const sm = new SessionManager(dbg);

    const health = await sm.health();

    expect(health).toMatchObject({
      state: "external_debugger_conflict",
      stale_attachment_count: 0,
      conflict_count: 1,
      connection_conflict: true,
      conflict_kind: "external_debugger",
      safe_to_cleanup: false,
      recommended_action: "close_external_debugger_or_choose_another_tab",
    });
  });

  it("auto-attaches a spawned target and resumes it (attach-race defense)", async () => {
    const dbg = fakeDebugger();
    const sm = new SessionManager(dbg);
    await sm.attachRoot(42);
    const events: any[] = [];
    sm.onTabEvent((e) => events.push(e));
    // simulate a click spawning a new tab with waitForDebuggerOnStart
    dbg.fire("Target.attachedToTarget", {
      sessionId: "S1",
      waitingForDebugger: true,
      targetInfo: { targetId: "T1", type: "page", url: "https://x/detail" },
    });
    await Promise.resolve();
    await Promise.resolve();
    // it must resume the frozen target
    expect(dbg.sendCommand).toHaveBeenCalledWith(
      expect.anything(),
      "Runtime.runIfWaitingForDebugger",
      undefined,
      "S1",
    );
    expect(
      events.find((e) => e.kind === "new-tab" && e.target_id === "T1"),
    ).toBeTruthy();
  });

  it("closeExcursion closes the spawned tab (cleanup-on-error)", async () => {
    const dbg = fakeDebugger();
    const sm = new SessionManager(dbg);
    await sm.attachRoot(42);
    dbg.fire("Target.attachedToTarget", {
      sessionId: "S1",
      waitingForDebugger: false,
      targetInfo: { targetId: "T1", type: "page", url: "https://x/detail" },
    });
    await Promise.resolve();
    await sm.closeExcursion("T1");
    expect(dbg.sendCommand).toHaveBeenCalledWith(
      expect.anything(),
      "Target.closeTarget",
      { targetId: "T1" },
      undefined,
    );
  });

  it("removes a closed controlled root tab while preserving other controlled tabs", async () => {
    const dbg = fakeDebugger();
    let seq = 0;
    (dbg.sendCommand as any).mockImplementation(async (_t: unknown, method: string) => {
      if (method === "Target.getTargetInfo") {
        seq += 1;
        return { targetInfo: { targetId: `T${seq}`, url: `https://x/${seq}` } };
      }
      return {};
    });
    const sm = new SessionManager(dbg);
    await sm.attachRoot(42);
    await sm.attachRoot(99);

    expect(sm.hasTab(42)).toBe(true);
    expect(sm.hasTab(99)).toBe(true);
    expect(sm.removeTab(99)).toBe(true);
    expect(sm.hasTab(99)).toBe(false);
    expect(sm.hasTab(42)).toBe(true);
    expect(sm.rootTab()).toBe(42);
  });

  it("keeps child targets scoped to the root tab that emitted the CDP event", async () => {
    const dbg = fakeDebugger();
    let seq = 0;
    (dbg.sendCommand as any).mockImplementation(async (_t: unknown, method: string) => {
      if (method === "Target.getTargetInfo") {
        seq += 1;
        return { targetInfo: { targetId: `T${seq}`, url: `https://x/${seq}` } };
      }
      return {};
    });
    const sm = new SessionManager(dbg);
    await sm.attachRoot(42);
    await sm.attachRoot(99);

    dbg.fire("Target.attachedToTarget", {
      sessionId: "S-old-root-child",
      waitingForDebugger: true,
      targetInfo: {
        targetId: "T-child-42",
        type: "page",
        url: "https://x/from-42",
      },
    }, 42);
    await Promise.resolve();
    await Promise.resolve();

    expect(sm.tabIdFor("T-child-42")).toBe(42);
    expect(dbg.sendCommand).toHaveBeenCalledWith(
      { tabId: 42 },
      "Runtime.runIfWaitingForDebugger",
      undefined,
      "S-old-root-child",
    );
    expect(sm.rootTarget()).toBe("T2");
  });
});
