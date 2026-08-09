import { describe, it, expect, vi } from "vitest";
import { routeCommand } from "./router";
import type { Envelope } from "../shared/envelope";

describe("routeCommand", () => {
  it("dispatches a command and emits an observation echoing id + tab + target_id", async () => {
    const sent: string[] = [];
    const sm = {
      knownTargets: () => ["T0"],
      tabIdFor: () => 7,
      send: vi.fn().mockResolvedValue({ data: "QUJD" }),
      closeExcursion: vi.fn(),
    } as any;
    const ov = {} as any;
    const env: Envelope = {
      v: 1,
      kind: "command",
      id: "c9",
      channel: "chat:1",
      transport: "t:b",
      producer: "agent",
      data: { cmd: "screenshot", args: { command_id: "cmd_app_1" }, target_id: "T0" },
    };
    await routeCommand(env, { sm, ov, sendObservation: (r) => sent.push(r) });
    const obs = JSON.parse(sent[0]);
    expect(obs.kind).toBe("observation");
    expect(obs.id).toBe("c9");
    expect(obs.data.ok).toBe(true);
    expect(obs.data.command_id).toBe("cmd_app_1");
    expect(obs.data.target_id).toBe("T0");
    expect(obs.data.tab).toBe(7); // stable tab id echoed back
    expect(obs.data.media[0].slot).toBe("screenshot");
  });

  it("emits ok:false on handler error rather than hanging", async () => {
    const sent: string[] = [];
    const sm = {
      knownTargets: () => ["T0"],
      tabIdFor: () => 7,
      send: vi.fn().mockRejectedValue(new Error("boom")),
      closeExcursion: vi.fn(),
    } as any;
    const env: Envelope = {
      v: 1,
      kind: "command",
      id: "c1",
      channel: "c",
      transport: "t",
      producer: "agent",
      data: { cmd: "navigate", args: { url: "x", command_id: "cmd_app_2" }, target_id: "T0" },
    };
    await routeCommand(env, {
      sm,
      ov: {} as any,
      sendObservation: (r) => sent.push(r),
    });
    const obs = JSON.parse(sent[0]);
    expect(obs.data.ok).toBe(false);
    expect(obs.data.command_id).toBe("cmd_app_2");
    expect(obs.data.error).toContain("boom");
    expect(obs.data.error_code).toBe("browser_command_result_unknown");
    expect(obs.data.effect_status).toBe("unknown");
  });

  it("marks a mutating handler precondition failure as not executed", async () => {
    const sent: string[] = [];
    const sm = {
      knownTargets: () => ["T0"],
      tabIdFor: () => 7,
      send: vi.fn().mockResolvedValue({ result: { value: { found: false } } }),
      closeExcursion: vi.fn(),
    } as any;
    const env: Envelope = {
      v: 1,
      kind: "command",
      id: "c3",
      channel: "c",
      transport: "t",
      producer: "agent",
      data: { cmd: "click", args: { handle: "missing" }, target_id: "T0" },
    };
    await routeCommand(env, { sm, ov: {} as any, sendObservation: (r) => sent.push(r) });
    const obs = JSON.parse(sent[0]);
    expect(obs.data.ok).toBe(false);
    expect(obs.data.error_code).toBe("browser_command_not_executed");
    expect(obs.data.not_executed).toBe(true);
  });

  it("emits structured tab_not_controlled when a stable tab is outside the session", async () => {
    const sent: string[] = [];
    const sm = {
      knownTargets: () => ["T0"],
      targetForTab: vi.fn().mockReturnValue(undefined),
      tabIdFor: () => undefined,
    } as any;
    const env: Envelope = {
      v: 1,
      kind: "command",
      id: "c2",
      channel: "c",
      transport: "t",
      producer: "agent",
      data: { cmd: "click", args: { tab: 42, command_id: "cmd_app_3" }, target_id: "" },
    };
    await routeCommand(env, {
      sm,
      ov: {} as any,
      sendObservation: (r) => sent.push(r),
    });
    const obs = JSON.parse(sent[0]);
    expect(obs.data.ok).toBe(false);
    expect(obs.data.command_id).toBe("cmd_app_3");
    expect(obs.data.error_code).toBe("tab_not_controlled");
    expect(obs.data.not_executed).toBe(true);
  });
});
