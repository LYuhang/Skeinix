/**
 * Convert an extension-internal browser-session event into the protocol exposed
 * to the embedded web UI. Chrome topology stays private; the UI only learns
 * whether the selected Chat may continue from this side-panel window.
 */
export function projectBrowserControlForWindow(
  input: Record<string, unknown>,
  currentWindowId: number | undefined,
): Record<string, unknown> {
  const status = String(input.status || "");
  const chatId = typeof input.chat_id === "string" ? input.chat_id : "";
  const terminal = status === "released" || status === "inactive";
  const eventWindow = String(input.browser_window_id ?? input.window_id ?? "");
  const {
    browser_session_id: _browserSessionId,
    session_id: _sessionId,
    session_generation: _sessionGeneration,
    event_seq: _eventSeq,
    browser_window_id: _browserWindowId,
    window_id: _windowId,
    panel_context_id: _panelContextId,
    ...safe
  } = input;
  void _browserWindowId;
  void _browserSessionId;
  void _sessionId;
  void _sessionGeneration;
  void _eventSeq;
  void _windowId;
  void _panelContextId;
  return {
    ...safe,
    browser_control_chat_id: terminal ? "" : chatId,
    browser_control_available_here:
      terminal ||
      (currentWindowId !== undefined && String(currentWindowId) === eventWindow),
  };
}
