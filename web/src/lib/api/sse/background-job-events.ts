import { fetchEventSource } from '@microsoft/fetch-event-source';
import { useEffect, useRef } from 'react';

import { getApiBase } from '@/lib/base-path';
import { useAuthStore } from '@/stores/auth';

export interface BackgroundJobEvent {
  event_id: number;
  job_id: string;
  seq: number;
  event_type: string;
  payload: Record<string, unknown>;
  created_at?: string | null;
}

export function useBackgroundJobEvents(
  scopeId: string,
  chatId: string,
  onEvent: (event: BackgroundJobEvent) => void,
) {
  const callbackRef = useRef(onEvent);
  useEffect(() => {
    callbackRef.current = onEvent;
  }, [onEvent]);

  useEffect(() => {
    if (!scopeId || !chatId) return;
    const controller = new AbortController();
    let cursor = 0;
    const base = getApiBase();
    const headers: Record<string, string> = {
      Accept: 'text/event-stream',
    };
    const token = useAuthStore.getState().token;
    if (token) headers.Authorization = `Bearer ${token}`;
    void fetchEventSource(
      `${base}/api/v1/chat-scopes/${encodeURIComponent(scopeId)}/chats/${encodeURIComponent(chatId)}/background-jobs/events`,
      {
        signal: controller.signal,
        credentials: 'include',
        openWhenHidden: false,
        headers,
        onopen: async (response) => {
          if (response.status === 401) {
            useAuthStore.getState().handle401();
            throw new Error('auth');
          }
          if (!response.ok) {
            throw new Error(`background job stream open failed: ${response.status}`);
          }
        },
        onmessage(message) {
          if (message.event !== 'background_job' || !message.data) return;
          try {
            const event = JSON.parse(message.data) as BackgroundJobEvent;
            if (!Number.isFinite(event.event_id) || !event.job_id) return;
            cursor = Math.max(cursor, event.event_id);
            headers['Last-Event-ID'] = String(cursor);
            callbackRef.current(event);
          } catch {
            // The snapshot query remains authoritative after a malformed frame.
          }
        },
        onclose() {
          if (!controller.signal.aborted) {
            throw new Error('background job stream closed');
          }
        },
        onerror(error) {
          if (controller.signal.aborted) throw error;
        },
      },
    ).catch(() => {
      // fetch-event-source reconnects transient failures. A remount/relogin
      // performs a fresh authoritative snapshot query and durable replay.
    });
    return () => controller.abort();
  }, [chatId, scopeId]);
}
