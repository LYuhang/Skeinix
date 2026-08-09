import { describe, it, expect } from "vitest";
import {
  CMD,
  ALL_CMDS,
  MUTATING,
  parseCommand,
  makeObservation,
} from "./commands";
import { decode, type Envelope } from "./envelope";

describe("command vocabulary (mirror of commands.py)", () => {
  it("is a closed string enum matching the Python values", () => {
    expect(CMD.CLICK).toBe("click");
    expect(CMD.ACQUIRE_VIDEO).toBe("acquire_video");
    expect(ALL_CMDS).toContain("read_fields");
    expect(ALL_CMDS).toContain("wait_for_new_tab");
  });
  it("marks the mutating boundary", () => {
    expect(MUTATING.has(CMD.SUBMIT)).toBe(true);
    expect(MUTATING.has(CMD.NAVIGATE)).toBe(true);
    expect(MUTATING.has(CMD.START_SESSION)).toBe(true);
    expect(MUTATING.has(CMD.CLOSE_TAB)).toBe(true);
    expect(MUTATING.has(CMD.SNAPSHOT)).toBe(false);
    expect(MUTATING.has(CMD.GET_ATTRIBUTE)).toBe(false);
  });
  it("parseCommand accepts a known command", () => {
    const env: Envelope = {
      v: 1,
      kind: "command",
      id: "c1",
      channel: "chat:1",
      transport: "t:b",
      producer: null,
      data: { cmd: "click", args: { handle: "h7" }, target_id: "tab9" },
    };
    const c = parseCommand(env);
    expect(c).toEqual({ cmd: "click", args: { handle: "h7" }, target_id: "tab9" });
  });
  it("parseCommand rejects an unknown command (no-remote-code gate)", () => {
    const env: Envelope = {
      v: 1,
      kind: "command",
      id: "c1",
      channel: "chat:1",
      transport: "t:b",
      producer: null,
      data: { cmd: "exec_js", args: {}, target_id: "tab9" },
    };
    expect(() => parseCommand(env)).toThrow();
  });
  it("makeObservation builds a kind=observation envelope", () => {
    const raw = makeObservation("c1", "chat:1", "t:b", {
      ok: true,
      target_id: "tab9",
      text: "hi",
    });
    const d = decode(raw);
    expect(d.kind).toBe("observation");
    expect((d.data as Record<string, unknown>).ok).toBe(true);
    expect((d.data as Record<string, unknown>).target_id).toBe("tab9");
    expect((d.data as Record<string, unknown>).text).toBe("hi");
  });
});
