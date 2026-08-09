/**
 * MV3 background service worker.
 *
 * Receives the web app's AUTH_SYNC over `externally_connectable`
 * (auth-only account sharing — no chat handoff), and relay the side-panel
 * embed's OPEN_WS to the offscreen document (which holds the socket, B1).
 *
 * CDP command execution lives here because `chrome.debugger` is available
 * in the service worker but NOT in offscreen documents): run commands relayed
 * from the offscreen (`RUN_COMMAND`), attach the CDP session to a tab (`ATTACH`),
 * relay narration/highlight messages to the target tab's Dynamic Island, and push tab events
 * to the WS via the offscreen (`WS_SEND`).
 *
 * Robustness note (§23): the SW is disposable. Session identity and controlled
 * tab ids live in storage.session; a replacement worker adopts debugger targets
 * that are still attached, or re-attaches tabs whose debugger was detached.
 */
import { SessionManager, type Debugger } from "./cdp/session-manager";
import { type Overlay } from "./cdp/handlers";
import { routeCommand } from "./cdp/router";
import { encode } from "./shared/envelope";
import { isAllowedWebAppSenderUrl, WS_BASE } from "./shared/config";

// ---- CDP layer (service-worker context — chrome.debugger lives here) ----

const chromeDebugger: Debugger = {
  attach: (tabId) => chrome.debugger.attach({ tabId }, "1.3"),
  detach: (tabId) => chrome.debugger.detach({ tabId }),
  sendCommand: (t, method, params, sessionId) =>
    // `sessionId` is the CDP flat-mode session for an auto-attached child target
    // (§9) — a real chrome.debugger.Debuggee field at runtime; @types/chrome
    // predates it, so we widen the target shape here.
    chrome.debugger.sendCommand(
      { ...t, sessionId } as unknown as chrome.debugger.Debuggee,
      method,
      params,
    ),
  onEvent: (cb) =>
    chrome.debugger.onEvent.addListener((source, method, params) =>
      cb(source, method, params),
    ),
  getTargets: () =>
    chrome.debugger.getTargets() as unknown as Promise<
      Array<{ id: string; tabId?: number; type: string; attached?: boolean }>
    >,
};
const sm = new SessionManager(chromeDebugger);

// The window the side panel is docked in (reported by sidepanel.ts on mount /
// focus). When we adopt "the user's current tab" we scope the active-tab query to
// THIS window — otherwise `currentWindow` from the (windowless) service worker
// resolves to the last-focused window, which can be a DIFFERENT browser window
// than the one the user opened the side panel in. Mirrored in storage.session so
// it survives a SW restart.
let activePanelWindowId: number | undefined;
let activePanelContextId: string | undefined;
let browserSessionEventSeq = 0;
type BrowserSessionState = {
  sessionId?: string;
  channel?: string;
  transport?: string;
  chatId?: string;
  browserWindowId?: string;
  panelContextId?: string;
  sessionGeneration?: number;
};
let currentBrowserSession: BrowserSessionState | null = null;

// MV3 service workers can restart while the offscreen WebSocket and debugger
// session continue. Session identity and the event sequence are therefore
// durable extension state, not process-local variables.
const browserSessionStateReady = chrome.storage.session
  .get([
    "browserSessionEventSeq",
    "currentBrowserSession",
    "activePanelWindowId",
    "activePanelContextId",
  ])
  .then((stored) => {
    const seq = Number(stored.browserSessionEventSeq || 0);
    browserSessionEventSeq = Number.isFinite(seq) && seq >= 0 ? seq : 0;
    const session = stored.currentBrowserSession;
    if (session && typeof session === "object") {
      currentBrowserSession = session as BrowserSessionState;
    }
    if (typeof stored.activePanelWindowId === "number") {
      activePanelWindowId = stored.activePanelWindowId;
    }
    if (typeof stored.activePanelContextId === "string") {
      activePanelContextId = stored.activePanelContextId;
    }
  })
  .catch(() => undefined);

async function setCurrentBrowserSession(
  session: BrowserSessionState | null,
): Promise<void> {
  await browserSessionStateReady;
  currentBrowserSession = session;
  if (session) {
    await chrome.storage.session.set({ currentBrowserSession: session });
    // A newly reserved generation proves any older terminal event has already
    // been reconciled by the backend; do not replay it into the new session.
    await chrome.storage.session.remove("pendingBrowserSessionTerminalEvent");
  } else {
    await chrome.storage.session.remove("currentBrowserSession");
  }
}

async function rememberedControlledTabIds(): Promise<number[]> {
  const stored = await chrome.storage.session.get([
    "controlledTabId",
    "controlledTabIds",
  ]);
  const ids = Array.isArray(stored.controlledTabIds)
    ? stored.controlledTabIds.filter(
        (value: unknown): value is number =>
          typeof value === "number" && Number.isInteger(value) && value >= 0,
      )
    : [];
  if (typeof stored.controlledTabId === "number") ids.push(stored.controlledTabId);
  return [...new Set(ids)];
}

async function persistControlledTabs(): Promise<void> {
  const ids = [...new Set(sm.knownTabs().map((tab) => tab.tab))];
  if (ids.length === 0) {
    await chrome.storage.session.remove(["controlledTabId", "controlledTabIds"]);
    return;
  }
  await chrome.storage.session.set({
    controlledTabId: ids[0],
    controlledTabIds: ids,
  });
}

async function restoreRememberedControlledSession(): Promise<boolean> {
  if (sm.knownTargets().length > 0) return true;
  const rememberedTabs = await rememberedControlledTabIds();
  let restoredAny = false;
  for (const tabId of rememberedTabs) {
    try {
      await sm.attachRoot(tabId);
      restoredAny = true;
    } catch {
      // A closed tab does not invalidate the remaining remembered tabs.
    }
  }
  if (restoredAny) {
    await persistControlledTabs();
    void setIsland(true, "ready");
  }
  return restoredAny;
}

function chatIdFromChannel(channel?: string): string {
  return channel?.startsWith("chat:") ? channel.slice("chat:".length) : "";
}

const CANCELLED_TURN_TTL_MS = 10 * 60 * 1000;
const cancelledTurnKeys = new Set<string>();

function cancelledTurnKey(chatId: string, turnId: string): string {
  return `${chatId}:${turnId}`;
}

function rememberCancelledTurn(chatId: string, turnId: string): void {
  const key = cancelledTurnKey(chatId, turnId);
  cancelledTurnKeys.add(key);
  setTimeout(() => cancelledTurnKeys.delete(key), CANCELLED_TURN_TTL_MS);
}

function isCancelledTurn(chatId: string, turnId: string): boolean {
  return cancelledTurnKeys.has(cancelledTurnKey(chatId, turnId));
}

let browserSessionEventQueue: Promise<void> = Promise.resolve();

function broadcastBrowserSessionChanged(payload: Record<string, unknown>): Promise<void> {
  const requestedSession = currentBrowserSession
    ? { ...currentBrowserSession }
    : null;
  const task = browserSessionEventQueue.then(async () => {
    await browserSessionStateReady;
    const session = requestedSession || currentBrowserSession;
    const seq = ++browserSessionEventSeq;
    await chrome.storage.session.set({ browserSessionEventSeq: seq });
  const enriched = {
    ...payload,
    session_id:
      typeof payload.session_id === "string"
        ? payload.session_id
        : session?.sessionId || "",
    browser_session_id:
      typeof payload.browser_session_id === "string"
        ? payload.browser_session_id
        : session?.sessionId || "",
    session_generation:
      typeof payload.session_generation === "number"
        ? payload.session_generation
        : session?.sessionGeneration || 0,
    chat_id:
      typeof payload.chat_id === "string"
        ? payload.chat_id
        : session?.chatId || chatIdFromChannel(session?.channel),
    window_id:
      payload.window_id ?? session?.browserWindowId ?? "",
    browser_window_id:
      payload.browser_window_id ?? session?.browserWindowId ?? "",
    panel_context_id:
      payload.panel_context_id ?? session?.panelContextId ?? "",
    event_seq: seq,
  };
  const status = String(payload.status || "");
  if (status === "released" || status === "inactive") {
    // Keep the terminal event until the iframe acknowledges its fenced HTTP
    // write. If the WS was offline and no panel was open, replay it on reconnect.
    await chrome.storage.session.set({
      pendingBrowserSessionTerminalEvent: enriched,
    });
  }
  void chrome.runtime.sendMessage({
    type: "BROWSER_SESSION_CHANGED",
    ...enriched,
  }).catch(() => {
    // No side panel currently open; the backend event below is still attempted.
  });
  try {
    const backendEvent: Record<string, unknown> = { ...enriched };
    delete backendEvent.window_id;
    delete backendEvent.browser_window_id;
    delete backendEvent.panel_context_id;
    await chrome.runtime.sendMessage({
      type: "WS_SEND",
      raw: encode("event", {
        id: `evt_browser_session_${Date.now()}_${seq}`,
        channel: session?.channel || "system",
        transport: session?.transport || "pending",
        data: {
          type: "browser_session_changed",
          ...backendEvent,
        },
      }),
    });
  } catch {
    // WS may be down; the iframe release fallback and command errors still apply.
  }
  if (status === "released" || status === "inactive") {
      await setCurrentBrowserSession(null);
  }
  });
  browserSessionEventQueue = task.catch(() => undefined);
  return task;
}

function sendBrowserSessionSnapshot(): Promise<void> {
  const task = browserSessionEventQueue.then(async () => {
    await browserSessionStateReady;
    const session = currentBrowserSession;
    if (!session?.sessionId || !session.chatId || !session.sessionGeneration) return;
    await restoreRememberedControlledSession();
    const seq = ++browserSessionEventSeq;
    await chrome.storage.session.set({ browserSessionEventSeq: seq });
    await chrome.runtime.sendMessage({
      type: "WS_SEND",
      raw: encode("event", {
        id: `evt_browser_snapshot_${Date.now()}_${seq}`,
        channel: session.channel || `chat:${session.chatId}`,
        transport: session.transport || "pending",
        data: {
          type: "browser_session_snapshot",
          chat_id: session.chatId,
          browser_session_id: session.sessionId,
          session_generation: session.sessionGeneration,
          event_seq: seq,
          controlled: sm.knownTargets().length > 0,
          tab_ids: sm.knownTabs().map((tab) => tab.tab),
          reason: "websocket_reconnected",
        },
      }),
    });
  });
  browserSessionEventQueue = task.catch(() => undefined);
  return task;
}

async function replayPendingBrowserTerminalEvent(): Promise<void> {
  const stored = await chrome.storage.session.get("pendingBrowserSessionTerminalEvent");
  const event = stored.pendingBrowserSessionTerminalEvent;
  if (!event || typeof event !== "object") return;
  const record = event as Record<string, unknown>;
  const chatId = typeof record.chat_id === "string" ? record.chat_id : "";
  if (!chatId) return;
  const backendEvent = { ...record };
  delete backendEvent.window_id;
  delete backendEvent.browser_window_id;
  delete backendEvent.panel_context_id;
  await chrome.runtime.sendMessage({
    type: "WS_SEND",
    raw: encode("event", {
      id: `evt_browser_terminal_replay_${Date.now()}`,
      channel: `chat:${chatId}`,
      transport: "reconnect",
      data: {
        type: "browser_session_changed",
        ...backendEvent,
      },
    }),
  });
}

async function releaseControlledBrowserSession(
  reason: string,
  extra: Record<string, unknown> = {},
): Promise<void> {
  await browserSessionStateReady;
  const tabs = [
    ...new Set([
      ...sm.knownTabs().map((x) => x.tab),
      ...(await rememberedControlledTabIds()),
    ]),
  ];
  sessionReleaseInProgress = true;
  sm.reset();
  await chrome.storage.session
    .remove(["controlledTabId", "controlledTabIds"])
    .catch(() => {});
  void setIsland(false);
  try {
    for (const tabId of tabs) {
      try {
        await chrome.debugger.detach({ tabId });
      } catch {
        // Already detached or closed; release is best-effort for every tab.
      }
    }
  } finally {
    sessionReleaseInProgress = false;
  }
  await broadcastBrowserSessionChanged({
    status: "released",
    reason,
    tab_ids: tabs,
    ...extra,
  });
}

// Page-feedback proxies to the Dynamic Island content script in the targeted
// controlled tab. Never use "active tab" here: in a multi-tab browser session the
// agent may be operating on a background tab, and the side panel's service worker
// has no reliable currentWindow of its own.
async function relayToTargetTab(
  targetId: string,
  msg: unknown,
): Promise<unknown> {
  const tabId = sm.tabIdFor(targetId);
  if (tabId === undefined) return undefined;
  return new Promise((res) => chrome.tabs.sendMessage(tabId, msg, res));
}
const ov: Overlay = {
  highlight: async (targetId, sel, label) => {
    await relayToTargetTab(targetId, { type: "PAGE_HIGHLIGHT", selector: sel, label });
  },
  narrate: async (targetId, text) => {
    await relayToTargetTab(targetId, { type: "PAGE_NARRATE", text });
  },
};

// Tab events (new-tab / tab-closed) → push to the backend over the WS (held by
// the offscreen) as `kind:"event"` frames so the host can correlate excursions.
sm.onTabEvent((e) => {
  // Child tab ids are resolved asynchronously from CDP target ids; persist on
  // the next short tick so a service-worker restart can recover every attached
  // top-level tab, not only the original root.
  setTimeout(() => void persistControlledTabs(), 100);
  void chrome.runtime.sendMessage({
    type: "WS_SEND",
    raw: encode("event", {
      id: `evt_${Date.now()}`,
      channel: "system",
      transport: "pending",
      data: e,
    }),
  });
});

// Keep-alive: the persistent offscreen holds a long-lived port to us. While that
// port is connected the SW is NOT evicted — so the chrome.debugger session it
// holds isn't released (which would drop the "debugging" banner + the attach,
// forcing a re-attach after every command). §23 robustness.
chrome.runtime.onConnect.addListener((port) => {
  if (port.name === "keepalive") {
    // Each inbound ping resets the SW idle timer (the actual keep-alive).
    port.onMessage.addListener(() => void 0);
    port.onDisconnect.addListener(() => void 0);
  }
});

// Clicking the toolbar icon opens the side panel DIRECTLY (no popup / no extra
// "open side panel" click). Requires an `action` in the manifest. Idempotent —
// safe to call on every SW spawn; wrapped so an old Chrome without the API can't
// break startup.
try {
  void chrome.sidePanel
    ?.setPanelBehavior?.({ openPanelOnActionClick: true })
    .catch((e) => console.warn("[skeinix] setPanelBehavior failed", e));
} catch {
  /* sidePanel API unavailable — non-fatal */
}

// chrome.debugger attaches per page-target; a cross-origin navigation swaps the
// renderer process and DESTROYS the old target → onDetach fires "target_closed"
// (NOT SW eviction — the keep-alive handles that). Same-origin navigation within
// one tool does not do this. To make the controlled session FOLLOW the tab across
// such navigations (instead of waiting for the next command to re-attach), we
// re-attach immediately if the tab still exists. Re-attachment is tracked per
// tab: two controlled tabs can navigate/close concurrently and neither event may
// suppress the other one's lifecycle handling.
const reattachingTabs = new Set<number>();
let sessionReleaseInProgress = false;
async function handleDebuggerDetach(tabId: number, reason: string): Promise<void> {
  // SessionManager registers its own chrome.debugger listener first and removes
  // the live target mapping before this lifecycle listener runs. The persisted
  // tab set is therefore part of the ownership check, not merely restart data.
  const wasControlled =
    sm.hasTab(tabId) || (await rememberedControlledTabIds()).includes(tabId);
  if (!wasControlled || sessionReleaseInProgress) return;
  if (reason === "target_closed") {
    // Chrome may report the same target transition more than once. Only dedupe
    // the same tab; transitions for other controlled tabs remain independent.
    if (reattachingTabs.has(tabId)) return;
    reattachingTabs.add(tabId);
    sm.removeTab(tabId);
    chrome.tabs.get(tabId, (tab) => {
      if (chrome.runtime.lastError || !tab) {
        reattachingTabs.delete(tabId); // tab really gone
        const remaining = sm.knownTabs();
        if (remaining.length === 0) {
          void persistControlledTabs();
          void setIsland(false);
          void broadcastBrowserSessionChanged({
            status: "released",
            reason: "last_tab_closed",
            tabId,
          });
        } else {
          void persistControlledTabs();
          void setIsland(true, "ready");
        }
        return;
      }
      // small delay so the new post-navigation target is ready
      setTimeout(() => {
        void sm
          .attachRoot(tabId)
          .then(() => void setIsland(true, "ready")) // reattached → still controlled
          .catch(() => {
            if (sm.knownTabs().length === 0) {
              void setIsland(false);
              void broadcastBrowserSessionChanged({
                status: "lost",
                reason: "reattach_failed",
                tabId,
              });
            }
          })
          .finally(() => {
            reattachingTabs.delete(tabId);
            void persistControlledTabs();
          });
      }, 300);
    });
  } else {
    // User clicked the debugger banner's Cancel or DevTools replaced the
    // debugger. Product semantics: cancelling any controlled tab releases the
    // whole Browser Session, not just that tab.
    void releaseControlledBrowserSession(String(reason || "debugger_detached"), { tabId });
  }
}

chrome.debugger.onDetach.addListener((source, reason) => {
  console.warn("[skeinix] debugger detached", { tabId: source.tabId, reason });
  // Check synchronously as well as inside the async handler. During an explicit
  // end_session, detach events may be delivered after the handler has started;
  // they must not schedule a second terminal release.
  if (sessionReleaseInProgress) return;
  const tabId = source.tabId;
  if (tabId === undefined) return;
  void handleDebuggerDetach(tabId, String(reason || "debugger_detached"));
});

/** Re-attach to the remembered controlled tab if the session was lost (SW reborn
 *  / debugger detached), so commands keep working without a manual re-attach. */
async function ensureAttached(opts?: { preferNewTab?: boolean; windowId?: number }): Promise<void> {
  if (sm.knownTargets().length > 0) return;
  if (await restoreRememberedControlledSession()) return;
  // Resolve the side panel's OWN window — BOTH paths below scope to it so we act
  // in the window the user opened the side panel in, not whichever window happens
  // to be focused (the SW has no real "current window").
  const windowId = opts?.windowId ?? activePanelWindowId;
  if (windowId === undefined) {
    throw new Error("browser_window_unavailable");
  }
  // No controlled tab yet. DEFAULT (side-panel copilot model): adopt the page the
  // user is CURRENTLY looking at — when they say "look at this page", spawning a
  // fresh about:blank tab is wrong. Attach to the active real web page (its
  // debugger banner + the island appear there → control is visible on the page
  // they meant). The agent opts OUT via browser_start_session(target="new") for a
  // NEW task that shouldn't disturb the user's page. We also fall back to a fresh
  // tab when the active tab isn't a drivable web page (chrome://, extension pages).
  if (!opts?.preferNewTab) {
    try {
      const [active] = await chrome.tabs.query({ active: true, windowId });
      if (
        active?.id !== undefined &&
        !!active.url &&
        /^(https?|file):/.test(active.url)
      ) {
        await sm.attachRoot(active.id);
        await persistControlledTabs();
        void setIsland(true, "ready");
        return;
      }
    } catch {
      // couldn't adopt the active tab — fall through to a fresh tab
    }
  }
  try {
    // Open the fresh tab IN the side panel's window too, so a new-task tab doesn't
    // land in some other window.
    const tab = await chrome.tabs.create(
      windowId !== undefined
        ? { url: "about:blank", active: true, windowId }
        : { url: "about:blank", active: true },
    );
    if (tab.id !== undefined) {
      // give the new tab a moment so its CDP target exists before we attach
      await new Promise((r) => setTimeout(r, 250));
      await sm.attachRoot(tab.id);
      await persistControlledTabs();
      void setIsland(true, "ready");
    }
  } catch {
    // couldn't create/attach — the command will report ok:false with the error
  }
}

// ---- Authentication handshake ----

/**
 * Ensure exactly one offscreen document exists before we forward `OPEN_WS`.
 * Reason: Chrome's `offscreen` API has no "WebSocket" reason; `WEB_RTC` is the
 * closest valid member for a long-lived socket that must outlive the SW.
 */
async function ensureOffscreen(): Promise<void> {
  const has = await chrome.offscreen.hasDocument();
  if (has) return;
  await chrome.offscreen.createDocument({
    url: "offscreen.html",
    reasons: [chrome.offscreen.Reason.WEB_RTC],
    justification:
      "Hold the tenant-scoped backend WebSocket across service-worker lifecycles.",
  });
}

interface AuthSyncMsg {
  type: "AUTH_SYNC" | "AUTH_CLEAR";
  /** Single-use code redeemed by the iframe for a partitioned HttpOnly Session.
   * The primary Web Session is never stored by the extension. */
  exchangeCode: string;
  tenant?: string;
  /** The main app's resolved model settings (credential_id, temperature,
   *  max_tokens, timeout), relayed so the embedded chat runs with the same
   *  credential and generation parameters. Stored under
   *  `embedAgentSettings` and echoed back via GET_BINDING / BINDING. */
  agentSettings?: Record<string, unknown>;
}

// One-way migration from the pre-cookie implementation. A primary Web
// Session must never survive in extension storage after this worker starts.
void chrome.storage.local.remove("embedSessionToken");

/**
 * The browser-id is STABLE per browser profile (persisted), not per handoff:
 * it identifies the WS connection / this browser, so the backend can route a
 * chat turn's browser tools to this exact transport across reconnects and new
 * handoffs. Generate once with crypto.randomUUID and reuse forever.
 */
async function getStableBrowserId(): Promise<string> {
  const { browserId } = await chrome.storage.local.get("browserId");
  if (typeof browserId === "string" && browserId) return browserId;
  const id = crypto.randomUUID();
  await chrome.storage.local.set({ browserId: id });
  return id;
}

/** Minimal SW-side view of the connection, surfaced to the side panel. */
const state: { connected: boolean; wfId: string } = {
  connected: false,
  wfId: "",
};

/**
 * Last binding pushed to the side-panel iframe: the embedded chat is
 * linked to the controlled browser by `(wf_id, chat_id, browser_id)`. browser_id
 * is the stable per-browser id; chat_id may be empty until the embed creates one.
 */
const binding: {
  wf_id: string;
  chat_id: string;
  browser_id: string;
  /** App Relay — the in-flight instruction relayed from the main app
   *  for the embed to auto-send once. Empty when not relayed (entry B). */
  instruction: string;
  /** The web app's full origin+path-prefix base (from the handoff), so the
   *  side panel builds the /embed/chat URL with the proxy prefix. "" → fall back
   *  to the bundled WEB_BASE. */
  webBase: string;
  /** The WS base (ws(s)://host[/prefix]) from the handoff. The embed's own
   *  OPEN_WS relay doesn't carry it, so we fall back to THIS stored value —
   *  otherwise the offscreen builds a relative URL and WebSocket throws
   *  "scheme … chrome-extension … not allowed". */
  wsBase: string;
} = {
  wf_id: "",
  chat_id: "",
  browser_id: "",
  instruction: "",
  webBase: "",
  wsBase: "",
};

// NOTE: webBase/wsBase no longer come from a runtime handoff — they are BAKED
// into the bundle (WEB_BASE / WS_BASE in shared/config), set per-environment at
// build time. So the binding no longer needs to survive SW eviction; the side
// panel builds its /embed/chat URL from the baked WEB_BASE and the offscreen
// reconnects with the baked WS_BASE. Auth is synced separately (AUTH_SYNC).

/**
 * Best-effort write of the Dynamic Island state the content script subscribes to
 * (`chrome.storage.session.islandState`). Schema:
 *   { controlled, kind: "ready"|"thinking"|"tool"|"browser_tool"|"streaming"|"confirm", tool? }
 *
 * VISIBILITY is gated by `controlled` ONLY (true = the CDP debugger is attached
 * and controlling; false/absent = the island is hidden) — it tracks the
 * attach/detach lifecycle, NOT WS connect. `kind` is the content; in THIS task
 * the SW only writes "ready" (controlled + idle) and "browser_tool" (with `tool`
 * = the cmd name) while a browser command runs. The thinking/tool/streaming/
 * confirm kinds are written by the chat UI in a later task.
 *
 * Wrapped so a missing session store (or eviction race) can never break command
 * execution.
 */
async function setIsland(
  controlled: boolean,
  kind?: string,
  tool?: string,
): Promise<void> {
  try {
    await chrome.storage.session.set({
      islandState: { controlled, kind: kind ?? "ready", tool },
    });
  } catch {
    // session storage unavailable — the island just won't update; non-fatal.
  }
}

// Account sync from the web app (auth ONLY — not a chat handoff). The main app
// pushes a one-time exchange code so the side-panel embed can create its own
// partitioned HttpOnly Session. Stored briefly under `embedExchangeCode` /
// `embedAgentSettings`; the side panel reads them via GET_BINDING / BINDING.
// Transport (webBase/wsBase) now comes from the baked config, not a handoff.
chrome.runtime.onMessageExternal.addListener(
  (msg: unknown, sender, sendResponse: (r: unknown) => void) => {
    const m = msg as Partial<AuthSyncMsg> | null;
    if (m?.type !== "AUTH_SYNC" && m?.type !== "AUTH_CLEAR") return false;
    if (!isAllowedWebAppSenderUrl(sender.url)) {
      sendResponse({ ok: false });
      return false;
    }

    void (async () => {
      if (m.type === "AUTH_CLEAR") {
        await chrome.storage.local.remove([
          "embedExchangeCode",
          "embedAgentSettings",
        ]);
        // Logout/account switch is a session-level close: detach every tab,
        // persist + publish the terminal event under the old session identity,
        // then close the old account's socket.
        await releaseControlledBrowserSession("auth_clear");
        await chrome.runtime.sendMessage({ type: "CLOSE_WS" }).catch(() => undefined);
        sendResponse({ ok: true });
        return;
      }
      if (typeof m.exchangeCode === "string" && m.exchangeCode)
        await chrome.storage.local.set({ embedExchangeCode: m.exchangeCode });
      if (m.agentSettings && typeof m.agentSettings === "object")
        await chrome.storage.local.set({ embedAgentSettings: m.agentSettings });
      sendResponse({ ok: true });
    })();

    return true; // keep the channel open for the async sendResponse
  },
);

async function requestAuthSyncFromOpenAppTabs(): Promise<void> {
  const tabs = await chrome.tabs.query({});
  const appTabs = tabs.filter((tab) => isAllowedWebAppSenderUrl(tab.url));
  if (appTabs.length === 0) return;

  // The content-script event causes the Web app to fetch a fresh exchange code
  // and send AUTH_SYNC back through onMessageExternal. Wait briefly for that
  // asynchronous round-trip; absence/failure falls back to independent login.
  await new Promise<void>((resolve) => {
    let settled = false;
    const finish = () => {
      if (settled) return;
      settled = true;
      chrome.storage.onChanged.removeListener(onChanged);
      resolve();
    };
    const onChanged = (changes: Record<string, chrome.storage.StorageChange>, area: string) => {
      if (area === "local" && changes.embedExchangeCode?.newValue) finish();
    };
    chrome.storage.onChanged.addListener(onChanged);
    void Promise.allSettled(appTabs.map(async (tab) => {
      if (typeof tab.id !== "number") return;
      // Dispatch in the page's MAIN world. A declarative content script lives
      // in an isolated world: sending a message to it can succeed even when a
      // CustomEvent it dispatches does not reliably wake the application's
      // listener. That false success was why an authenticated main app could
      // still leave the side panel on its login screen. `host_permissions` is
      // generated from the exact externally_connectable allowlist, so this
      // never executes on arbitrary sites.
      await chrome.scripting.executeScript({
        target: { tabId: tab.id },
        world: "MAIN",
        func: () => {
          document.dispatchEvent(
            new CustomEvent("skeinix:extension-auth-refresh"),
          );
        },
      });
    }));
    setTimeout(finish, 1_500);
  });
}

// Connection lifecycle relayed up from the offscreen document.
chrome.runtime.onMessage.addListener((msg: unknown) => {
  const m = msg as { type?: string; echo?: unknown } | null;
  if (m?.type === "WS_OPEN") {
    const recovered = !state.connected;
    state.connected = true;
    if (recovered && sm.knownTargets().length > 0) {
      void setIsland(true, "recovered");
    }
    void replayPendingBrowserTerminalEvent()
      .then(() => sendBrowserSessionSnapshot())
      .catch(() => undefined);
  }
  if (m?.type === "WS_CLOSED") {
    state.connected = false;
    if (sm.knownTargets().length > 0) void setIsland(true, "disconnected");
  }
  if (m?.type === "WS_ECHO") {
    const echo = m.echo as Record<string, unknown> | undefined;
    if (echo?.type === "browser_session_event_ack") {
      void (async () => {
        const stored = await chrome.storage.session.get(
          "pendingBrowserSessionTerminalEvent",
        );
        const pending = stored.pendingBrowserSessionTerminalEvent as
          | Record<string, unknown>
          | undefined;
        if (
          pending &&
          String(pending.browser_session_id || "") === String(echo.browser_session_id || "") &&
          Number(pending.session_generation || 0) === Number(echo.session_generation || 0) &&
          Number(pending.event_seq || 0) === Number(echo.event_seq || 0)
        ) {
          await chrome.storage.session.remove("pendingBrowserSessionTerminalEvent");
        }
      })();
    }
    console.debug("[skeinix] echo", m.echo);
  }
  return false;
});

// ---- Command execution, attach, and stop ----
chrome.runtime.onMessage.addListener(
  (msg: unknown, _sender, sendResponse: (r: unknown) => void) => {
    const m = msg as {
      type?: string;
      tabId?: number;
      windowId?: number;
      panelContextId?: string;
      chat_id?: string;
      turn_id?: string;
      browser_session_id?: string;
      session_generation?: number;
      event_seq?: number;
      env?: unknown;
    } | null;
    const type = m?.type;

    if (type === "AUTH_EXCHANGE_CONSUMED") {
      // Until this ACK arrives GET_BINDING/REQUEST_BINDING may replay the
      // one-time code so an iframe-load race cannot strand the side panel.
      void chrome.storage.local.remove("embedExchangeCode");
      sendResponse({ ok: true });
      return true;
    }

    if (type === "SIDEPANEL_WINDOW") {
      // The side panel tells us which window it lives in, so "adopt the current
      // tab" targets the active tab of THAT window (not whichever window happens
      // to be focused). Remember it in memory + storage (SW can restart).
      if (
        typeof m?.windowId === "number" &&
        typeof m.panelContextId === "string" &&
        m.panelContextId
      ) {
        activePanelWindowId = m.windowId;
        activePanelContextId = m.panelContextId;
        void chrome.storage.session.set({
          activePanelWindowId: m.windowId,
          activePanelContextId: m.panelContextId,
        });
      }
      return false;
    }

    if (type === "ISLAND_PHASE") {
      // The chat UI relays its current stream phase (ready/thinking/
      // tool/browser_tool/streaming) via the side-panel shell. Visibility stays
      // gated by debugger control, so only apply the phase while a tab is
      // actually controlled; otherwise the island must stay hidden. Coexists
      // with the RUN_COMMAND-driven updates (last-writer-wins). Best-effort.
      try {
        const p = m as unknown as { kind?: string; tool?: string };
        if (sm.knownTargets().length > 0) void setIsland(true, p.kind, p.tool);
      } catch {
        // never let a relayed phase break the SW
      }
      return false; // no async response
    }

    if (type === "SET_LANG") {
      // Persist the language chosen in embedded settings so the
      // island content script (which reads chrome.storage.local.lang) renders in
      // the same language. Best-effort, validated to the closed zh/en set.
      try {
        const lang = (m as unknown as { lang?: string }).lang;
        if (lang === "zh" || lang === "en")
          void chrome.storage.local.set({ lang });
      } catch {
        // session/local storage unavailable — non-fatal
      }
      return false; // no async response
    }

    if (type === "SET_THEME") {
      try {
        const theme = (m as unknown as { theme?: string }).theme;
        if (theme === "light" || theme === "dark")
          void chrome.storage.local.set({ theme });
      } catch {
        // Theme projection is presentation-only and must never disrupt control.
      }
      return false;
    }

    if (type === "BROWSER_TURN_CANCELLED") {
      const chatId = typeof m?.chat_id === "string" ? m.chat_id : "";
      const turnId = typeof m?.turn_id === "string" ? m.turn_id : "";
      if (chatId && turnId) {
        rememberCancelledTurn(chatId, turnId);
        sendResponse({ ok: true });
        return true;
      }
      sendResponse({ ok: false, error: "missing chat_id or turn_id" });
      return true;
    }

    if (type === "RUN_COMMAND") {
      // Re-attach if the session was lost (SW reborn / debugger detached), then
      // run CDP here and reply with the observation raw string (offscreen sends
      // it back over the WS).
      void (async () => {
        // First-attach tab choice is the agent's call: `browser_start_session`
        // carries `target="new"` — open a FRESH blank tab (new task, don't
        // disturb the user's page); current/absent adopts the page the user is
        // currently looking at. Non-session commands never attach implicitly.
        const cenv = m!.env as {
          channel?: string;
          transport?: string;
          data?: { cmd?: string; args?: Record<string, unknown> };
        };
        const cmd = cenv?.data?.cmd;
        const args = cenv?.data?.args ?? {};
        const channel = typeof cenv.channel === "string" ? cenv.channel : "";
        const chatId = chatIdFromChannel(channel);
        const turnId = typeof args.turn_id === "string" ? args.turn_id : "";
        const commandId =
          typeof args.command_id === "string"
            ? args.command_id
            : String((m!.env as { id?: unknown }).id || "");
        let observationSent = false;
        const sendCommandError = (
          error_code: string,
          error: string,
          extra: Record<string, unknown> = {},
        ) => {
          observationSent = true;
          sendResponse(encode("observation", {
            id: String((m!.env as { id?: unknown }).id || ""),
            channel: String((m!.env as { channel?: unknown }).channel || ""),
            transport: String((m!.env as { transport?: unknown }).transport || ""),
            data: {
              ok: false,
              command_id: commandId,
              error_code,
              error,
              error_info: { not_executed: true, ...extra },
              not_executed: true,
              ...extra,
            },
          }));
        };
        const expectedWindowIdFromArgs = (): number | undefined => {
          const raw = currentBrowserSession?.browserWindowId ?? activePanelWindowId;
          const n = typeof raw === "number" ? raw : raw ? Number(raw) : NaN;
          return Number.isFinite(n) ? n : undefined;
        };
        const rejectTabOutsideWindow = async (tabId: number): Promise<boolean> => {
          const expectedWindowId = expectedWindowIdFromArgs();
          if (!Number.isFinite(expectedWindowId)) return false;
          let tab: chrome.tabs.Tab;
          try {
            tab = await chrome.tabs.get(tabId);
          } catch {
            sendCommandError("tab_not_found", `Tab ${tabId} was not found.`);
            return true;
          }
          if (tab.windowId !== expectedWindowId) {
            sendCommandError(
              "tab_out_of_scope",
              "The requested tab is not in this side panel's browser window.",
            );
            return true;
          }
          return false;
        };
        try {
          await browserSessionStateReady;
          if (chatId && turnId && isCancelledTurn(chatId, turnId)) {
            sendCommandError(
              "turn_cancelled",
              "This Agent turn was cancelled by the user before the browser command started.",
            );
            return;
          }
          const expectedWindowId = expectedWindowIdFromArgs();
          if (expectedWindowId === undefined) {
            sendCommandError(
              "browser_window_unavailable",
              "The extension cannot resolve its active side-panel window. Reopen the side panel before controlling the browser.",
            );
            return;
          }
          // Window scope is an extension-local concern. Enrich the local command
          // before routing it to Chrome; the backend/Agent never receives or
          // stores this browser topology.
          args.browser_window_id = expectedWindowId;
          if (cmd === "start_session") {
            await setCurrentBrowserSession({
              sessionId:
                typeof args.browser_session_id === "string" ? args.browser_session_id : "",
              channel,
              transport: typeof cenv.transport === "string" ? cenv.transport : "",
              chatId,
              browserWindowId: String(expectedWindowId),
              panelContextId: activePanelContextId,
              sessionGeneration:
                typeof args.session_generation === "number"
                  ? args.session_generation
                  : typeof args.session_generation === "string"
                    ? Number(args.session_generation)
                    : 0,
            });
          }
          if (
            cmd !== "start_session" &&
            cmd !== "list_open_tabs" &&
            typeof args.browser_session_id === "string" &&
            currentBrowserSession?.sessionId &&
            args.browser_session_id !== currentBrowserSession.sessionId
          ) {
            sendCommandError(
              "browser_session_mismatch",
              "The browser command belongs to an old browser session and was not executed.",
            );
            return;
          }
          if (
            cmd !== "start_session" &&
            cmd !== "list_open_tabs" &&
            args.session_generation != null &&
            currentBrowserSession?.sessionGeneration != null &&
            Number(args.session_generation) !== currentBrowserSession.sessionGeneration
          ) {
            sendCommandError(
              "browser_session_mismatch",
              "The browser command belongs to an old browser session generation and was not executed.",
            );
            return;
          }
          const wantNewTab = cmd === "start_session" && args.target === "new";
          const isStartSession = cmd === "start_session";
          const isSessionOptionalRead = cmd === "list_open_tabs";
          const skipAutoAttach = isSessionOptionalRead;
          if (
            !isStartSession &&
            !skipAutoAttach &&
            sm.knownTargets().length === 0 &&
            currentBrowserSession?.sessionId &&
            currentBrowserSession.sessionId === args.browser_session_id &&
            Number(currentBrowserSession.sessionGeneration || 0) ===
              Number(args.session_generation || 0)
          ) {
            await restoreRememberedControlledSession();
          }
          if (
            isStartSession &&
            args.target === "existing" &&
            (typeof args.tab === "number" || typeof args.tab === "string")
          ) {
            const tabId = Number(args.tab);
            if (await rejectTabOutsideWindow(tabId)) return;
            await sm.attachRoot(tabId);
            await persistControlledTabs();
            void setIsland(true, "ready");
          } else if (isStartSession) {
            await ensureAttached({ preferNewTab: wantNewTab, windowId: expectedWindowId });
          } else if (
            !skipAutoAttach &&
            cmd !== "end_session" &&
            sm.knownTargets().length === 0
          ) {
            sendCommandError(
              "browser_session_released",
              "Browser control is not active. Start a browser session before running this command.",
            );
            return;
          }
          if (
            cmd !== "start_session" &&
            cmd !== "list_open_tabs" &&
            cmd !== "end_session" &&
            args.tab != null &&
            args.tab !== "" &&
            await rejectTabOutsideWindow(Number(args.tab))
          ) {
            return;
          }
          // Reflect the in-flight browser command as the island's `browser_tool`
          // kind, but ONLY when a tab is actually attached/controlled — the island's
          // visibility follows the debugger lifecycle (it does NOT hide between
          // commands; that happens on detach). The content script maps the cmd name
          // to a friendly bilingual label, so we pass the raw cmd (no text).
          const controlled = sm.knownTargets().length > 0;
          if (controlled) {
            void setIsland(true, "browser_tool", cenv?.data?.cmd);
          }
          const routeWillReleaseSession = cmd === "end_session";
          if (routeWillReleaseSession) sessionReleaseInProgress = true;
          await routeCommand(m!.env as Parameters<typeof routeCommand>[0], {
            sm,
            ov,
            sendObservation: (raw) => {
              observationSent = true;
              sendResponse(raw);
            },
          });
          if (cmd !== "end_session" && sm.knownTargets().length > 0) {
            await persistControlledTabs();
          }
          if (cmd === "start_session" && sm.knownTargets().length > 0) {
            await broadcastBrowserSessionChanged({
              status: "attached",
              reason: "agent_started",
            });
          }
          if (cmd === "end_session") {
            sm.reset();
            await chrome.storage.session.remove([
              "controlledTabId",
              "controlledTabIds",
            ]);
            await setIsland(false);
            sessionReleaseInProgress = false;
            await broadcastBrowserSessionChanged({
              status: "released",
              reason:
                typeof args.reason === "string" && args.reason
                  ? args.reason
                  : "agent_requested",
            });
          }
          // Command done → back to idle/ready. Re-check AFTER the command: a
          // `use_tab` may have JUST adopted the user's tab (uncontrolled → controlled
          // in this command), so the island should appear now even though `controlled`
          // was false before it ran.
          if (sm.knownTargets().length > 0) void setIsland(true, "ready");
        } catch (e) {
          if (cmd === "end_session") sessionReleaseInProgress = false;
          const error = String((e as Error)?.message || e);
          if (cmd === "start_session") {
            sm.reset();
            await chrome.storage.session
              .remove(["controlledTabId", "controlledTabIds"])
              .catch(() => {});
            await broadcastBrowserSessionChanged({
              status: "released",
              reason: "start_session_failed",
              error,
            });
          }
          if (!observationSent) {
            sendCommandError(
              cmd === "start_session" ? "browser_start_session_failed" : "browser_command_failed",
              error,
            );
          }
        }
      })();
      return true; // async sendResponse
    }

    if (type === "GET_BINDING") {
      // The side-panel iframe asks for the current binding so it can build its
      // /embed/chat URL. We also hand back a one-time Session exchange code.
      void (async () => {
        await browserSessionStateReady;
        const browserId = await getStableBrowserId();
        const { embedExchangeCode, embedAgentSettings } =
          await chrome.storage.local.get([
            "embedExchangeCode",
            "embedAgentSettings",
          ]);
        sendResponse({
          wf_id: binding.wf_id,
          browser_id: browserId,
          // A cold side panel starts without a relayed chat id. Once the
          // backend routes browser_start_session on `chat:<id>`, that durable
          // command gives the extension an authoritative local projection of
          // the controlled Chat. Reuse it on shell/iframe reload so the embed
          // resumes the same server-owned history instead of minting a new
          // conversation.
          chat_id: currentBrowserSession?.chatId || binding.chat_id,
          browser_control_chat_id: currentBrowserSession?.chatId || "",
          browser_control_available_here:
            !currentBrowserSession?.chatId ||
            (typeof m?.windowId === "number" &&
              String(m.windowId) === String(currentBrowserSession.browserWindowId || "")),
          // App Relay: the relayed instruction for the embed to
          // auto-send once. Empty when not relayed (entry B → no auto-send).
          instruction: binding.instruction,
          webBase: binding.webBase,
          exchangeCode:
            typeof embedExchangeCode === "string" ? embedExchangeCode : "",
          agentSettings:
            embedAgentSettings && typeof embedAgentSettings === "object"
              ? embedAgentSettings
              : undefined,
        });
      })();
      return true; // async sendResponse
    }

    if (type === "REQUEST_BINDING") {
      // Entry B: the iframe (relayed by the side-panel shell) requests a fresh
      // binding broadcast. Same payload as GET_BINDING.
      void (async () => {
        await browserSessionStateReady;
        const browserId = await getStableBrowserId();
        const { embedExchangeCode, embedAgentSettings } =
          await chrome.storage.local.get([
            "embedExchangeCode",
            "embedAgentSettings",
          ]);
        sendResponse({
          type: "BINDING",
          wf_id: binding.wf_id,
          browser_id: browserId,
          chat_id: currentBrowserSession?.chatId || binding.chat_id,
          browser_control_chat_id: currentBrowserSession?.chatId || "",
          browser_control_available_here:
            !currentBrowserSession?.chatId ||
            (typeof m?.windowId === "number" &&
              String(m.windowId) === String(currentBrowserSession.browserWindowId || "")),
          // App Relay: the relayed instruction for the embed to
          // auto-send once. Empty when not relayed (entry B → no auto-send).
          instruction: binding.instruction,
          webBase: binding.webBase,
          exchangeCode:
            typeof embedExchangeCode === "string" ? embedExchangeCode : "",
          agentSettings:
            embedAgentSettings && typeof embedAgentSettings === "object"
              ? embedAgentSettings
              : undefined,
        });
      })();
      return true; // async sendResponse
    }

    if (type === "REQUEST_AUTH_REFRESH") {
      void (async () => {
        await requestAuthSyncFromOpenAppTabs();
        const browserId = await getStableBrowserId();
        const { embedExchangeCode, embedAgentSettings } =
          await chrome.storage.local.get(["embedExchangeCode", "embedAgentSettings"]);
        sendResponse({
          type: "BINDING",
          wf_id: binding.wf_id,
          browser_id: browserId,
          chat_id: currentBrowserSession?.chatId || binding.chat_id,
          browser_control_chat_id: currentBrowserSession?.chatId || "",
          browser_control_available_here:
            !currentBrowserSession?.chatId ||
            (typeof m?.windowId === "number" &&
              String(m.windowId) === String(currentBrowserSession.browserWindowId || "")),
          instruction: binding.instruction,
          webBase: binding.webBase,
          exchangeCode:
            typeof embedExchangeCode === "string" ? embedExchangeCode : "",
          agentSettings:
            embedAgentSettings && typeof embedAgentSettings === "object"
              ? embedAgentSettings
              : undefined,
        });
      })();
      return true;
    }

    // Entry B: the iframe relays `{type:"OPEN_WS", scopedToken}` (via the side
    // panel shell). We open the WS through the offscreen. NOTE: the SW→offscreen
    // relay below uses `token` (not `scopedToken`), so this branch — keyed on
    // `scopedToken` — never re-fires on its own relay (no loop).
    const o = m as unknown as {
      type?: string;
      wsBase?: string;
      scopedToken?: string;
      browser?: string;
    };
    if (type === "OPEN_WS" && typeof o.scopedToken === "string") {
      void (async () => {
        await ensureOffscreen();
        const browser = o.browser || (await getStableBrowserId());
        binding.browser_id = browser;
        const opened = await chrome.runtime.sendMessage({
          type: "OPEN_WS",
          // The embed's relay often omits wsBase → fall back to the handoff's
          // stored value, then to the baked WS_BASE (a COLD entry-B open has
          // neither) so the offscreen never builds a relative (chrome-
          // extension://) URL.
          wsBase: o.wsBase || binding.wsBase || WS_BASE,
          token: o.scopedToken,
          browser,
        });
        sendResponse({
          ok:
            typeof opened === "object" &&
            opened !== null &&
            (opened as { ok?: unknown }).ok === true,
        });
      })();
      return true; // async sendResponse
    }

    if (type === "ATTACH") {
      // Attach the CDP root to a tab (the controlled target, D0.1) + remember it
      // so ensureAttached can transparently re-attach after a detach/SW-rebirth.
      void sm
        .attachRoot(m!.tabId as number)
        .then((targetId) => {
          void persistControlledTabs();
          void setIsland(true, "ready"); // controlled → show the island
          sendResponse({ ok: true, targetId });
        })
        .catch((e) => sendResponse({ ok: false, error: String(e) }));
      return true;
    }

    if (type === "STOP") {
      // Island Stop cancels the current Agent Turn via the side-panel iframe.
      // It must not release browser control or close the browser WebSocket.
      void chrome.runtime.sendMessage({ type: "BROWSER_STOP_REQUESTED" }).catch(() => {
        // No side panel currently open; there is no active composer to cancel.
      });
      sendResponse({ ok: true });
      return true;
    }

    return false;
  },
);

// ---------------------------------------------------------------------------
// Dev console harness (`self.__bdbg`). Drive the CDP layer STRAIGHT from the
// service-worker devtools console — bypassing the backend, the WS, and the
// offscreen relay entirely. This decouples "is browser control itself working?"
// (this harness) from "is the backend/protocol delivering commands?" (the full
// RUN_COMMAND path), so you can bisect a failure to a single layer.
//
// Open it: chrome://extensions → Skeinix → "service worker" (Inspect). Then:
//
//   await __bdbg.attach()                       // open + attach a controlled tab
//   __bdbg.targets()                            // ['<targetId>', ...]
//   await __bdbg.run('navigate', { url: 'https://example.com' })
//   await __bdbg.run('snapshot')                // → decoded observation object
//   await __bdbg.run('read_text')
//   await __bdbg.run('click', { ref: 'e12' })
//   await __bdbg.detach()                        // release control + banner
//
// Each run() builds a REAL command envelope and pushes it through the SAME
// routeCommand path RUN_COMMAND uses (parseCommand → dispatch → chrome.debugger),
// returning the decoded `{ ok, target_id, ... }` observation. Reachable only
// from the SW devtools (web pages cannot touch SW module globals), so it carries
// no extra attack surface.
(self as unknown as Record<string, unknown>).__bdbg = {
  async attach(): Promise<string[]> {
    await ensureAttached();
    return sm.knownTargets();
  },
  targets(): string[] {
    return sm.knownTargets();
  },
  async detach(): Promise<string[]> {
    for (const controlledTabId of await rememberedControlledTabIds()) {
      try {
        await chrome.debugger.detach({ tabId: controlledTabId });
      } catch {
        /* already detached / tab gone */
      }
    }
    sm.reset();
    await chrome.storage.session.remove(["controlledTabId", "controlledTabIds"]);
    void setIsland(false);
    return sm.knownTargets();
  },
  async run(
    cmd: string,
    args: Record<string, unknown> = {},
    targetId?: string,
  ): Promise<unknown> {
    await ensureAttached();
    const target_id = targetId || sm.knownTargets()[0] || "";
    const env = JSON.parse(
      encode("command", {
        id: `dbg_${Date.now()}`,
        channel: "debug",
        transport: "console",
        data: { cmd, args, target_id },
      }),
    );
    return new Promise((resolve) => {
      void routeCommand(env, {
        sm,
        ov,
        sendObservation: (raw) => resolve(JSON.parse(raw)),
      });
    });
  },
};
