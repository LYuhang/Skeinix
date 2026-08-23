/**
 * Task event SSE hook — drives the `/tasks/:id` detail page live log.
 *
 * Why a hook over the chat-stream / exec-stream zustand stores: the
 * task detail page is a single-task scope (one task, one stream, one
 * mount), and the events list is local UI state that does not need to
 * survive a route change. Lifting into a global store would be ceremony
 * without payoff; the hook keeps the component self-contained.
 *
 * Why `@microsoft/fetch-event-source` and not the native `EventSource`:
 *   - We must send `Last-Event-ID` on reconnect so the backend's
 *     replay-from-cursor contract works (T13's strict-ordering
 *     guarantee). Vanilla `EventSource` cannot set request headers, so
 *     it cannot transmit the cursor at all — the browser keeps that
 *     header for its own auto-reconnect, which is fine for in-flight
 *     drops but does not work for our hook-driven reconnect.
 *   - The native API also can't pass `Authorization` headers; the
 *     same rationale as `exec-stream.ts` / `agent-stream.ts`.
 *
 * Lifecycle:
 *   1. On mount (or `taskId` change), reset state and open the SSE
 *      connection.
 *   2. Each `onmessage` appends a `TaskEventFrame` to local state and
 *      bumps `lastIdRef` so a reconnect resumes from the cursor.
 *   3. Terminal frames (`terminal`) flip `done=true`
 *      and abort the request so the library stops retrying.
 *   4. Unmount aborts the controller; the library's `onerror` then sees
 *      `signal.aborted` and exits cleanly.
 *
 * The hook does NOT poll `GET /tasks/{id}` — the page uses TanStack
 * Query for that with a status-aware `refetchInterval`. SSE provides
 * the event log; polling provides the canonical status snapshot.
 */
import { useEffect, useRef, useState } from "react";
import { fetchEventSource } from "@microsoft/fetch-event-source";
import { isSseDoneSentinel } from "./json";

import { useAuthStore } from "@/stores/auth";
import { getApiBase } from "@/lib/base-path";
import type { TaskEventPayload, TaskEventType } from "@/lib/api/tasks";

/** One row from `task_events`, plus the parsed payload JSON. */
export interface TaskEventFrame {
  /** Monotonic `task_events.id` — strictly increasing per insertion. */
  id: number;
  /** Event name (`state` | `progress` | `log` | `result` | `terminal`). */
  event_type: TaskEventType;
  /** Parsed `data:` payload. `null` if the frame had no body. */
  payload: TaskEventPayload;
}

/**
 * Terminal event types — once we see one, the task is done and the
 * stream is finished. We abort the controller so the library stops its
 * auto-retry loop.
 */
const TERMINAL_EVENT_TYPES = new Set<string>([
  "terminal",
]);

const TASK_EVENT_TYPES = new Set<string>([
  "state",
  "progress",
  "log",
  "result",
  "terminal",
]);

export interface UseTaskStreamResult {
  /** Events in arrival order — same as `task_events.id` order on the wire. */
  events: TaskEventFrame[];
  /** `true` once a terminal frame has arrived. */
  done: boolean;
}

export function useTaskStream(
  taskId: string | undefined,
  enabled = true,
  initialAfter = 0,
): UseTaskStreamResult {
  const [streamState, setStreamState] = useState<{
    taskId: string;
    events: TaskEventFrame[];
    done: boolean;
  } | null>(null);
  /** Cursor for resume — sent as `Last-Event-ID` on reconnect. */
  const lastIdRef = useRef<number>(0);

  useEffect(() => {
    if (!taskId || !enabled) return;

    lastIdRef.current = initialAfter;

    const ctrl = new AbortController();
    const base = getApiBase();

    void (async function connect() {
      try {
        await fetchEventSource(`${base}/api/v1/tasks/${taskId}/stream`, {
          signal: ctrl.signal,
          credentials: "include",
          // Backend reads bearer token from the auth header — same as
          // the other SSE hooks. `credentials: "include"` is for cookie
          // sessions, which we don't use; keep the explicit Authorization.
          headers: (() => {
            const h: Record<string, string> = {
              Accept: "text/event-stream",
            };
            const token = useAuthStore.getState().token;
            if (token) h.Authorization = `Bearer ${token}`;
            if (lastIdRef.current > 0) {
              h["Last-Event-ID"] = String(lastIdRef.current);
            }
            return h;
          })(),
          // Keep streaming when the tab is backgrounded — a long batch
          // run can take minutes and the user often tabs away.
          openWhenHidden: true,
          onopen: async (res) => {
            // 401 short-circuits before the body is read; matches the
            // agent-stream + exec-stream pattern.
            if (res.status === 401) {
              useAuthStore.getState().handle401();
              throw new Error("auth");
            }
            if (!res.ok) {
              throw new Error(`task stream open failed: ${res.status}`);
            }
          },
          onmessage(msg) {
            if (isSseDoneSentinel(msg.data)) return;
            // The backend always emits an `id:` line; skip frames that
            // somehow lose it (defence in depth — the SSE generator in
            // `sse_bridge.py` always sets it from `task_events.id`).
            if (!msg.id) return;
            const id = Number(msg.id);
            if (Number.isNaN(id)) return;
            lastIdRef.current = Math.max(lastIdRef.current, id);

            let payload: unknown = {};
            if (msg.data) {
              try {
                payload = JSON.parse(msg.data);
              } catch {
                // Backend sends JSON, but keep the raw string as a
                // last resort so the UI can still display malformed
                // frames instead of swallowing them.
                payload = msg.data;
              }
            }

            const eventType = msg.event || "message";
            if (!TASK_EVENT_TYPES.has(eventType)) return;
            const frame: TaskEventFrame = {
              id,
              event_type: eventType as TaskEventType,
              payload: (payload && typeof payload === "object" ? payload : {}) as TaskEventPayload,
            };
            setStreamState((current) => ({
              taskId,
              events: current?.taskId === taskId ? [...current.events, frame] : [frame],
              done: current?.taskId === taskId ? current.done : false,
            }));

            if (TERMINAL_EVENT_TYPES.has(eventType)) {
              setStreamState((current) => ({
                taskId,
                events: current?.taskId === taskId ? current.events : [frame],
                done: true,
              }));
              // Abort so fetch-event-source stops its retry loop —
              // we've seen a terminal event and the backend will
              // close the stream anyway.
              ctrl.abort();
            }
          },
          onerror(err) {
            // If the controller is aborted (unmount or terminal frame),
            // re-throw so fetchEventSource exits. Otherwise return
            // undefined so the library applies its default backoff and
            // retries — the backend's resume-from-Last-Event-ID
            // contract makes that safe.
            if (ctrl.signal.aborted) throw err;
            // fall through → library default retry
          },
        });
      } catch {
        // Swallow — either an explicit abort (unmount / done) or an
        // unrecoverable error. The component already shows the task
        // status from the polled `getTask`, so a noisy toast here
        // would be redundant. Future: surface a small "stream lost"
        // banner if telemetry shows users hitting this often.
      }
    })();

    return () => {
      ctrl.abort();
    };
  }, [enabled, initialAfter, taskId]);

  if (!streamState || streamState.taskId !== taskId) return { events: [], done: false };
  return { events: streamState.events, done: streamState.done };
}
