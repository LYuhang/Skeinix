// Account sync to the MV3 extension via externally_connectable. The main app
// pushes a single-use, 60-second exchange code; the iframe redeems it for a
// partitioned HttpOnly cookie. The primary Web Session never enters JS or
// chrome.storage.
// This is AUTH-ONLY — no chat_id / instruction / mode handoff
// (the old cross-app chat relay was dropped; `/browser` is side-panel-only).
//
// `chrome` is typed via `as any` so the web app needs no @types/chrome dep.
import { buildAgentSettings } from '@/lib/api/sse/agent-stream';
import { getApiBase } from '@/lib/base-path';
import { sessionFetch } from '@/lib/api/session-fetch';

let syncGeneration = 0;

// Deterministic id derived from the fixed public `key` in extension/manifest.json
// — stable for every user/install/update. Override via VITE_EXTENSION_ID only if
// you load a differently-keyed build (e.g. an unkeyed dev unpack).
export function extensionId(): string | null {
  const env = (import.meta as { env?: Record<string, string> }).env ?? {};
  return env.VITE_EXTENSION_ID || 'mkfldhmlgdbpmhplaphhcfcdcoaakcik';
}

export function extensionOrigin(): string | null {
  const id = extensionId();
  return id ? `chrome-extension://${id}` : null;
}

/**
 * Push the current account to the extension so the side-panel embed shares the
 * main app's login. Best-effort and idempotent: no extension reachable (no
 * `chrome.runtime`, not installed) → silently no-op. Safe to call repeatedly
 * (on login + on boot + on token change).
 */
export async function syncAuthToExtension(
  authenticated: boolean,
  tenant: string | null,
): Promise<void> {
  const generation = ++syncGeneration;
  const extId = extensionId();
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const chrome = (globalThis as any).chrome;
  if (!chrome?.runtime?.sendMessage || !extId) return;
  if (!authenticated) {
    try {
      chrome.runtime.sendMessage(extId, { type: 'AUTH_CLEAR' }, () => {
        void chrome.runtime.lastError;
      });
    } catch {
      // Extension messaging unavailable — non-fatal.
    }
    return;
  }
  let exchangeCode: string;
  try {
    const response = await sessionFetch(
      `${getApiBase()}/api/v1/auth/extension/exchange-code`,
      { method: 'POST', credentials: 'include' },
    );
    if (!response.ok) return;
    const payload = await response.json() as { code?: unknown };
    exchangeCode = typeof payload.code === 'string' ? payload.code : '';
  } catch {
    return;
  }
  if (!exchangeCode || generation !== syncGeneration) return;
  // Relay model settings the same way the SSE builder resolves them, so the
  // embedded chat uses the same credential and generation parameters.
  const agentSettings = buildAgentSettings();
  try {
    chrome.runtime.sendMessage(
      extId,
      {
        type: 'AUTH_SYNC',
        exchangeCode,
        tenant: tenant ?? '',
        agentSettings,
      },
      () => {
        // Swallow chrome.runtime.lastError ("no receiving end") — best-effort.
        void chrome.runtime.lastError;
      },
    );
  } catch {
    // Extension messaging unavailable — non-fatal.
  }
}
