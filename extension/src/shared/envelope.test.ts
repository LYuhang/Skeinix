import { describe, it, expect } from "vitest";
import { encode, decode } from "./envelope";

describe("envelope", () => {
  it("round-trips and mirrors the Python envelope shape", () => {
    const raw = encode("ping", {
      id: "c1",
      channel: "chat:1",
      transport: "t:b",
      data: { hi: 1 },
    });
    expect(decode(raw)).toEqual({
      v: 1,
      kind: "ping",
      id: "c1",
      channel: "chat:1",
      transport: "t:b",
      data: { hi: 1 },
      producer: null,
    });
  });

  it("defaults data and producer to null (matches Python None)", () => {
    const raw = encode("echo", { id: "c2", channel: "system", transport: "t1:b1" });
    const d = decode(raw);
    expect(d.data).toBeNull();
    expect(d.producer).toBeNull();
    expect(d.v).toBe(1);
  });

  it("carries an explicit producer when provided", () => {
    const raw = encode("event", {
      id: "c3",
      channel: "chat:1",
      transport: "t1:b1",
      producer: "agent",
    });
    expect(decode(raw).producer).toBe("agent");
  });

  it("throws on non-JSON input", () => {
    expect(() => decode("nope")).toThrow();
  });

  it("throws when required fields are missing", () => {
    expect(() => decode(JSON.stringify({ kind: "x" }))).toThrow();
    expect(() =>
      decode(JSON.stringify({ kind: "x", id: "1", channel: "c" })),
    ).toThrow();
  });
});
