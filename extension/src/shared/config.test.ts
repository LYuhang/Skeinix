import { describe, expect, it } from "vitest";
import {
  ALLOWED_WEB_BASES,
  isAllowedWebAppSenderUrl,
  resolveAllowedWebBase,
  WEB_BASE,
} from "./config";

describe("externally connectable sender boundary", () => {
  it("accepts only the exact configured origin and reverse-proxy prefix", () => {
    const base = new URL(WEB_BASE);
    const prefix = base.pathname.replace(/\/+$/, "");
    expect(isAllowedWebAppSenderUrl(`${base.origin}${prefix}/workspace`)).toBe(true);
    expect(isAllowedWebAppSenderUrl(`${base.origin}${prefix || ""}`)).toBe(true);
    expect(isAllowedWebAppSenderUrl(`${base.origin}${prefix}-other/workspace`)).toBe(false);
    expect(isAllowedWebAppSenderUrl(`${base.protocol}//evil.${base.host}/workspace`)).toBe(false);
    expect(isAllowedWebAppSenderUrl(undefined)).toBe(false);
    expect(isAllowedWebAppSenderUrl("not a URL")).toBe(false);
    expect(ALLOWED_WEB_BASES).toContain(`${base.origin}${prefix}`);
    expect(resolveAllowedWebBase(WEB_BASE)).toBe(`${base.origin}${prefix}`);
    expect(resolveAllowedWebBase(`${base.origin}${prefix}/unexpected`)).toBeNull();
    expect(resolveAllowedWebBase("https://untrusted.example")).toBeNull();
  });
});
