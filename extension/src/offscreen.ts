/**
 * Offscreen document entry (B1). The offscreen page is the ONE place that holds
 * the backend WebSocket, because a persistent socket must outlive the MV3
 * service worker (which Chrome evicts when idle).
 *
 * It does NOT run CDP: `chrome.debugger` is unavailable in offscreen documents —
 * command execution runs in the SERVICE WORKER. On an inbound command frame the
 * offscreen relays the envelope to the SW (`RUN_COMMAND`) and sends the SW's
 * observation back over the WS. The SW can also push pre-encoded frames (e.g.
 * tab events) to the WS via `WS_SEND`.
 */
import { WsClient } from "./shared/ws-client";
import { browserWsProtocols } from "./shared/browser-ws-auth";

interface OpenWsMsg {
  type: "OPEN_WS";
  wsBase: string;
  token: string;
  browser: string;
}
interface PingMsg {
  type: "WS_PING";
  data: unknown;
}
interface WsSendMsg {
  type: "WS_SEND";
  raw: string;
}
interface CloseWsMsg {
  type: "CLOSE_WS";
}
type OffscreenMsg = OpenWsMsg | PingMsg | WsSendMsg | CloseWsMsg | { type?: undefined };

let client: WsClient | null = null;

// Keep the service worker alive while this (persistent) offscreen document
// exists, so the SW-held chrome.debugger session isn't released by SW eviction
// (which would drop the controlled-tab attach + the "debugging" banner). A
// connected port keeps the SW alive; we reconnect well under Chrome's 5-min cap.
function keepSwAlive(): void {
  const port = chrome.runtime.connect({ name: "keepalive" });
  // An open port alone is not reliably enough — periodic ACTIVITY on it resets
  // the SW's ~30s idle timer. Ping every 20s so the SW (and its chrome.debugger
  // session) never gets evicted while a session is active.
  const ping = setInterval(() => {
    try {
      port.postMessage("ping");
    } catch {
      /* port dead — onDisconnect will reconnect */
    }
  }, 20_000);
  port.onDisconnect.addListener(() => {
    clearInterval(ping);
    setTimeout(keepSwAlive, 1000);
  });
  setTimeout(() => {
    try {
      port.disconnect();
    } catch {
      /* already gone */
    }
  }, 240_000);
}
keepSwAlive();

chrome.runtime.onMessage.addListener(
  (msg: unknown, _sender, sendResponse: (r: unknown) => void) => {
    const m = msg as OffscreenMsg | null;

    if (m?.type === "OPEN_WS") {
      // Guard a missing/relative wsBase: without an absolute ws(s):// base the
      // URL would resolve against chrome-extension:// and `new WebSocket` throws
      // "scheme … not allowed". Bail loudly instead of crashing the handler.
      if (!/^wss?:\/\//i.test(m.wsBase ?? "")) {
        console.error("[offscreen] OPEN_WS got an invalid wsBase:", m.wsBase);
        sendResponse({ ok: false, error: "invalid wsBase" });
        return true;
      }
      const url = `${m.wsBase.replace(/\/+$/, "")}/api/v1/browser/ws`;
      let protocols: string[];
      try {
        protocols = browserWsProtocols(m.token, m.browser);
      } catch {
        sendResponse({ ok: false, error: "invalid browser WebSocket authentication" });
        return true;
      }
      // Exactly one socket per attached browser.
      client?.disconnect();
      client = new WsClient(url, protocols);
      client.onOpen(() => chrome.runtime.sendMessage({ type: "WS_OPEN" }));
      client.onClose(() => chrome.runtime.sendMessage({ type: "WS_CLOSED" }));
      client.onAuthRequired(() =>
        chrome.runtime.sendMessage({ type: "WS_AUTH_REQUIRED" }),
      );
      client.onEcho((echo) =>
        chrome.runtime.sendMessage({ type: "WS_ECHO", echo }),
      );
      // Command execution lives in the service worker (chrome.debugger is there,
      // not here). Relay each inbound command frame to the SW and send its
      // observation back over the WS. The host strips media bytes → VFS paths.
      client.onCommand((env) => {
        chrome.runtime.sendMessage({ type: "RUN_COMMAND", env }, (obsRaw) => {
          if (chrome.runtime.lastError) return; // SW gone; nothing to send back
          if (typeof obsRaw === "string") client?.sendRaw(obsRaw);
        });
      });
      client.connect();
      sendResponse({ ok: true });
      return true;
    }

    if (m?.type === "WS_PING") {
      const id = client?.ping(m.data);
      sendResponse({ ok: Boolean(id), id });
      return true;
    }

    if (m?.type === "WS_SEND") {
      // The SW pushes a pre-encoded frame (e.g. a tab event) to the backend.
      client?.sendRaw(m.raw);
      sendResponse({ ok: Boolean(client) });
      return false;
    }

    if (m?.type === "CLOSE_WS") {
      client?.disconnect();
      client = null;
      sendResponse({ ok: true });
      return false;
    }

    return false;
  },
);
