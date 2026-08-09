import { describe, expect, it } from "vitest";
import { projectBrowserControlForWindow } from "./browser-control-projection";

describe("browser-control frontend projection", () => {
  it("marks only the owning side-panel window writable and hides topology", () => {
    const event = {
      type: "BROWSER_SESSION_CHANGED",
      status: "attached",
      chat_id: "chat_1",
      browser_window_id: "42",
      window_id: "42",
      panel_context_id: "panel_secret",
      browser_session_id: "brs_1",
      session_id: "brs_1",
      session_generation: 3,
      event_seq: 9,
    };
    const owner = projectBrowserControlForWindow(event, 42);
    const other = projectBrowserControlForWindow(event, 43);

    expect(owner.browser_control_chat_id).toBe("chat_1");
    expect(owner.browser_control_available_here).toBe(true);
    expect(other.browser_control_available_here).toBe(false);
    for (const value of [owner, other]) {
      expect(value).not.toHaveProperty("browser_window_id");
      expect(value).not.toHaveProperty("window_id");
      expect(value).not.toHaveProperty("panel_context_id");
      expect(value).not.toHaveProperty("browser_session_id");
      expect(value).not.toHaveProperty("session_id");
      expect(value).not.toHaveProperty("session_generation");
      expect(value).not.toHaveProperty("event_seq");
    }
  });

  it("releases the local ownership projection", () => {
    const result = projectBrowserControlForWindow({
      status: "released",
      chat_id: "chat_1",
      browser_window_id: "42",
    }, 99);
    expect(result.browser_control_chat_id).toBe("");
    expect(result.browser_control_available_here).toBe(true);
  });
});
