/**
 * Offscreen-owned WebSocket client (B1: the socket lives in the offscreen
 * document so it survives service-worker eviction). Holds one connection to the
 * backend hub, replies to nothing on its own (the extension stays thin, §4.1) —
 * it just surfaces opens/echoes and reconnects with capped exponential backoff.
 *
 * On reconnect it loses no app state the backend can't re-drive: the host
 * registry re-keys the transport on the new socket. Ping, lifecycle, and
 * Playwright relay frames share the same transport.
 */
import { encode, decode, type Envelope } from "./envelope";

/**
 * Capped exponential backoff: 1s, 2s, 4s, 8s, 16s, then pinned at 30s.
 * Pure function so the curve is unit-testable without a live socket.
 */
export function backoffMs(attempt: number): number {
  return Math.min(1000 * 2 ** attempt, 30000);
}

let _seq = 0;
/** Process-unique correlation id for a ping (the id an echo will carry back). */
function corr(): string {
  return `c${Date.now()}_${_seq++}`;
}

export class WsClient {
  private ws: WebSocket | null = null;
  private attempt = 0;
  private closed = false;
  private heartbeat: ReturnType<typeof setInterval> | null = null;
  private openCbs: (() => void)[] = [];
  private closeCbs: ((event: CloseEvent) => void)[] = [];
  private authRequiredCbs: (() => void)[] = [];
  private echoCbs: ((e: Envelope) => void)[] = [];
  private playwrightRelayCbs: ((e: Envelope) => void)[] = [];

  constructor(
    private readonly url: string,
    private readonly protocols: readonly string[] = [],
  ) {}

  onOpen(cb: () => void): void {
    this.openCbs.push(cb);
  }

  onClose(cb: (event: CloseEvent) => void): void {
    this.closeCbs.push(cb);
  }

  onAuthRequired(cb: () => void): void {
    this.authRequiredCbs.push(cb);
  }

  onEcho(cb: (e: Envelope) => void): void {
    this.echoCbs.push(cb);
  }

  /** Subscribe to the authenticated Playwright extension relay data plane. */
  onPlaywrightRelay(cb: (e: Envelope) => void): void {
    this.playwrightRelayCbs.push(cb);
  }

  connect(): void {
    this.closed = false;
    const ws = new WebSocket(this.url, [...this.protocols]);
    this.ws = ws;

    ws.onopen = () => {
      this.attempt = 0; // reset backoff once we have a live socket
      this.stopHeartbeat();
      // The Runtime may spend tens of seconds reasoning before its first
      // browser call. Keep the transport active through reverse proxies and
      // jump-host tunnels during that idle period; the backend already answers
      // protocol pings with an echo.
      this.heartbeat = setInterval(() => {
        this.ping({ type: "keepalive" });
      }, 15_000);
      for (const cb of this.openCbs) cb();
    };

    ws.onmessage = (ev: MessageEvent) => {
      let e: Envelope;
      try {
        e = decode(String(ev.data));
      } catch {
        return; // ignore malformed frames; never eval server payloads (§6)
      }
      if (e.kind === "echo") {
        for (const cb of this.echoCbs) cb(e);
      } else if (e.kind === "playwright_relay") {
        for (const cb of this.playwrightRelayCbs) cb(e);
      }
    };

    ws.onclose = (event: CloseEvent) => {
      this.stopHeartbeat();
      if (this.closed) return; // intentional close: do not reconnect
      for (const cb of this.closeCbs) cb(event);
      if (event.code === 4401) {
        // The scoped capability expired or was revoked. Reusing it in a
        // reconnect loop can never succeed; ask the authenticated embed to mint
        // a fresh browser-bound capability without killing the browser session.
        this.closed = true;
        for (const cb of this.authRequiredCbs) cb();
        return;
      }
      const delay = backoffMs(this.attempt++);
      setTimeout(() => this.connect(), delay); // reconnect; host re-drives state
    };
  }

  /** Stop reconnecting and drop the socket. */
  disconnect(): void {
    this.closed = true;
    this.stopHeartbeat();
    this.ws?.close();
    this.ws = null;
  }

  private stopHeartbeat(): void {
    if (this.heartbeat !== null) {
      clearInterval(this.heartbeat);
      this.heartbeat = null;
    }
  }

  /**
   * Send a `ping` and return the correlation id the matching `echo` will carry.
   * `transport` is "pending" because the host stamps the authoritative
   * `transport_id` (`<tenant>:<browser>`) onto the echo it returns.
   */
  ping(data: unknown): string | null {
    if (this.ws?.readyState !== WebSocket.OPEN) return null;
    const id = corr();
    this.ws.send(
      encode("ping", { id, channel: "system", transport: "pending", data }),
    );
    return id;
  }

  /**
   * Send a pre-encoded lifecycle or Playwright relay frame verbatim. The
   * extension never constructs Agent run state, so this remains a thin
   * passthrough to the socket.
   */
  sendRaw(raw: string): void {
    this.ws?.send(raw);
  }
}
