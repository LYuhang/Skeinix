import { describe, it, expect } from "vitest";
import { ALL_CMDS } from "./commands";
import { PY_CMDS } from "./py-cmd-snapshot";

describe("R-C cross-language parity", () => {
  it("commands.ts matches commands.py exactly (no drift)", () => {
    expect([...ALL_CMDS].sort()).toEqual([...PY_CMDS].sort());
  });
});
