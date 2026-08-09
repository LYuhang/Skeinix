import { describe, it, expect, vi } from "vitest";
import { HANDLERS, dispatch } from "./handlers";
import { ALL_CMDS, CMD } from "../shared/commands";

function fakeSM() {
  return {
    send: vi.fn().mockImplementation(async (_t: string, method: string) => {
      if (method === "Page.captureScreenshot") return { data: "QUJD" }; // "ABC" b64
      if (method === "DOM.getDocument") return { root: { nodeId: 1 } };
      if (method === "DOM.querySelector") return { nodeId: 2 };
      if (method === "Runtime.evaluate")
        return { result: { value: { text: "hi", nodes: [] } } };
      return {};
    }),
    closeExcursion: vi.fn().mockResolvedValue(undefined),
    knownTargets: () => ["T0"],
  } as any;
}
const fakeOv = { highlight: vi.fn(), narrate: vi.fn() } as any;

describe("CDP handlers", () => {
  it("every command in the closed enum has a bundled handler (§6)", () => {
    for (const c of ALL_CMDS) expect(HANDLERS[c]).toBeTypeOf("function");
    expect(Object.keys(HANDLERS).sort()).toEqual([...ALL_CMDS].sort());
  });
  it("screenshot returns a media slot with b64 bytes (host turns into a path)", async () => {
    const out = await dispatch(CMD.SCREENSHOT, fakeSM(), fakeOv, "T0", {
      full_page: false,
    });
    expect(out.media).toEqual([
      { slot: "screenshot", b64: "QUJD", ext: "png", mime: "image/png" },
    ]);
  });
  it("snapshot injects the constant prune source", async () => {
    const sm = fakeSM();
    await dispatch(CMD.SNAPSHOT, sm, fakeOv, "T0", {});
    const call = sm.send.mock.calls.find(
      (c: any[]) => c[1] === "Runtime.evaluate",
    );
    expect(call[2].expression).toContain("skeinix-island-host"); // prune excludes extension UI
    expect(call[2].expression).toContain("_h"); // per-snapshot handle generation prefix
    expect(call[2].expression).not.toContain("${"); // constant, not interpolated
  });
  it("dispatch refuses an unknown command (no-remote-code)", async () => {
    await expect(
      dispatch("exec_js" as any, fakeSM(), fakeOv, "T0", {}),
    ).rejects.toThrow();
  });
  it("routes overlay commands with the target id selected by the router", async () => {
    const ov = {
      highlight: vi.fn().mockResolvedValue(undefined),
      narrate: vi.fn().mockResolvedValue(undefined),
    };
    await dispatch(CMD.HIGHLIGHT, fakeSM(), ov, "T42", {
      selector: "#cta",
      label: "CTA",
    });
    await dispatch(CMD.NARRATE, fakeSM(), ov, "T42", { text: "Working" });

    expect(ov.highlight).toHaveBeenCalledWith("T42", "#cta", "CTA");
    expect(ov.narrate).toHaveBeenCalledWith("T42", "Working");
  });
  it("end_session detaches each controlled browser tab exactly once", async () => {
    const detach = vi.fn().mockResolvedValue(undefined);
    const remove = vi.fn().mockResolvedValue(undefined);
    const previousChrome = (globalThis as any).chrome;
    (globalThis as any).chrome = {
      debugger: { detach },
      storage: { session: { remove } },
    };
    const sm = {
      knownTargets: () => ["T0", "T0-child", "T1"],
      tabIdFor: (targetId: string) =>
        targetId === "T0" || targetId === "T0-child" ? 42 : 99,
    } as any;

    try {
      const out = await dispatch(CMD.END_SESSION, sm, fakeOv, "T0", {});
      expect(detach.mock.calls.map(([arg]) => arg.tabId).sort()).toEqual([42, 99]);
      expect(out.released_tabs).toEqual([42, 99]);
      expect(remove).toHaveBeenCalledWith(["controlledTabId", "controlledTabIds"]);
    } finally {
      (globalThis as any).chrome = previousChrome;
    }
  });
});
