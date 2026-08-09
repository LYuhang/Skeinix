// Cancel an in-flight agent turn (the STOP button).
//
// Stop is backend-owned: the frontend asks the backend to cancel the turn, then
// keeps reading the SSE stream until the backend emits closure frames and the
// terminal `error(code="cancelled")` frame. That terminal signal resets the Stop
// button state via route-signal; the frontend must not locally fabricate closure.
import { useAuthStore } from '@/stores/auth';
import { getApiBase } from '@/lib/base-path';

/**
 * Cancel the backend-confirmed active Run for one Chat.
 *
 * The projection store is intentionally not consulted here: after reload,
 * reconnect, or rapid Chat switching its Turn id can be absent or stale while
 * PostgreSQL still owns a live Run. Stop is a control-plane operation, so the
 * backend active-runs view resolves the exact Chat→Run binding first.
 */
export async function cancelActiveTurn(
  chatId: string,
): Promise<{ chatId: string; turnId: string } | null> {
  if (!chatId) return null;
  const token = useAuthStore.getState().token;
  const base = getApiBase();
  try {
    const response = await fetch(
      `${base}/api/v1/chats/${encodeURIComponent(chatId)}/active-turn/cancel`,
      {
        method: 'POST',
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      },
    );
    if (response.status === 401) {
      useAuthStore.getState().handle401();
      return null;
    }
    if (!response.ok) return null;
    const payload = await response.json() as { chat_id?: unknown; run_id?: unknown };
    if (payload.chat_id !== chatId || typeof payload.run_id !== 'string') return null;
    return { chatId, turnId: payload.run_id };
  } catch {
    return null;
  }
}
