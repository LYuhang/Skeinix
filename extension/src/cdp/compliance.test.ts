// Web Store compliance (§6 — no remotely-hosted / dynamic code). These tests
// ENFORCE the invariant the design relies on: the backend can only trigger a
// FIXED, bundle-local operation declared ahead of time — never arbitrary code.
//
//  - Every command in the closed `Cmd` enum maps to a bundled handler. (This is
//    also enforced at compile time by `HANDLERS: Record<Cmd, Handler>`, but the
//    test makes the guarantee explicit and survives a type-cast regression.)
//  - `dispatch()` REJECTS anything without a bundled handler, so an unexpected
//    command name can never fall through to dynamic execution.
import { describe, it, expect } from "vitest";
import { CMD } from "../shared/commands";
import { HANDLERS, dispatch } from "./handlers";
import { SessionManager } from "./session-manager";

describe("no-remote-code compliance (§6)", () => {
  it("every declared command has a fixed, bundled handler (closed set)", () => {
    for (const cmd of Object.values(CMD)) {
      expect(typeof HANDLERS[cmd]).toBe("function");
    }
  });

  it("dispatch rejects any command without a bundled handler", async () => {
    const sm = new SessionManager({
      attach: async () => {},
      detach: async () => {},
      sendCommand: async () => ({}),
      onEvent: () => {},
      getTargets: async () => [],
    });
    const ov = {
      highlight: async () => {},
      narrate: async () => {},
    };
    await expect(
      // a command name that is deliberately NOT in the closed enum
      dispatch("arbitrary_injected_command" as never, sm, ov, "", {}),
    ).rejects.toThrow(/no bundled handler/);
  });
});
