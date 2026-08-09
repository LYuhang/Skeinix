/**
 * Browser WebSocket authentication contract.
 *
 * The scoped credential deliberately travels in the TLS-protected WebSocket
 * subprotocol offer rather than the URL, because URLs are routinely retained
 * by reverse-proxy access logs. The server selects only the public version
 * protocol and never echoes the credential-bearing offer.
 */
export const BROWSER_WS_PROTOCOL = "vibecanvas.browser.v1";

const AUTH_PREFIX = "vibecanvas.browser.auth.";
const BROWSER_PREFIX = "vibecanvas.browser.id.";
const SCOPED_TOKEN = /^[A-Za-z0-9._~-]{1,4096}$/;
const BROWSER_ID = /^[A-Za-z0-9._~-]{1,128}$/;

export function browserWsProtocols(token: string, browserId: string): string[] {
  if (!SCOPED_TOKEN.test(token)) {
    throw new Error("invalid browser WebSocket credential");
  }
  if (!BROWSER_ID.test(browserId)) {
    throw new Error("invalid browser identifier");
  }
  return [
    BROWSER_WS_PROTOCOL,
    `${AUTH_PREFIX}${token}`,
    `${BROWSER_PREFIX}${browserId}`,
  ];
}
