/**
 * SSE stream for `POST .../nodes/{node_id}/execute`.
 *
 * Mirrors `exec-stream.ts` (same `@microsoft/fetch-event-source` rationale:
 * POST + a JSON body + an `Authorization` header + an `AbortSignal`), but
 * writes to the dedicated `useNodeExecStore` so a node-debug run never
 * touches the workflow exec-stream store (canvas highlighting).
 *
 * The body carries the UNSAVED *draft* `node_dict` (M2): node-debug runs
 * the node the user is editing, NOT the committed snapshot, so the
 * frontend ships its local node payload. `input` is the node's inputs
 * supplied directly (no graph references — `run_node` does no reference
 * resolution).
 *
 * The backend synthesizes `running` → `completed` / `error` EXEC_UPDATE
 * frames (`services/node_exec.py`) fenced by `started` / `done` / `error`,
 * the SAME shape the workflow run uses — so the store + rendering stay
 * uniform across granularities.
 */
import { fetchEventSource } from '@microsoft/fetch-event-source';
import { useAuthStore } from '@/stores/auth';
import { getApiBase } from '@/lib/base-path';
import { useNodeExecStore } from '@/stores/node-exec';
import { queryClient } from '@/app/query-client';
import { isSseDoneSentinel, parseSseJson } from './json';

export interface StreamNodeExecutionArgs {
  wfId: string;
  nodeId: string;
  /** The draft node_dict the user is editing (M2). */
  node: Record<string, unknown>;
  input?: Record<string, unknown>;
  ac: AbortController;
  /** Receives the durable execution id used by the server-side cancel route. */
  onExecutionStarted?: (execId: string) => void;
}

function pickExecId(p: unknown): string {
  if (typeof p !== 'object' || p === null) return '';
  const o = p as Record<string, unknown>;
  if (typeof o.exec_id === 'string') return o.exec_id;
  if (typeof o.turn_id === 'string') return o.turn_id;
  if (typeof o.id === 'string') return o.id;
  return '';
}

/**
 * Run ONE node and stream its synthesized frames into `useNodeExecStore`.
 *
 * Resolves after `event: done`. Rejects on transport error or
 * `ac.abort()` (Stop button) — the caller downgrades an AbortError to
 * `status: 'cancelled'` without a toast.
 */
export async function streamNodeExecution(
  args: StreamNodeExecutionArgs,
): Promise<void> {
  const token = useAuthStore.getState().token;
  const base = getApiBase();
  const url =
    `${base}/api/v1/workflows/${args.wfId}/nodes/${args.nodeId}/execute`;

  // Begin immediately so the spinner shows even before `started` lands.
  useNodeExecStore.getState().begin(args.nodeId, args.ac, args.wfId);
  let announcedExecId = '';
  const announceExecId = (execId: string) => {
    if (!execId || execId === announcedExecId) return;
    announcedExecId = execId;
    args.onExecutionStarted?.(execId);
  };

  await fetchEventSource(url, {
    method: 'POST',
    credentials: 'include',
    headers: {
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      'Content-Type': 'application/json',
      Accept: 'text/event-stream',
    },
    body: JSON.stringify({ node: args.node, input: args.input ?? {} }),
    signal: args.ac.signal,
    openWhenHidden: true,
    async onopen(res) {
      if (res.status === 401) {
        useAuthStore.getState().handle401();
        throw new Error('auth');
      }
      if (!res.ok) {
        throw new Error(`node execution stream failed with HTTP ${res.status}`);
      }
      announceExecId(res.headers.get('X-Turn-Id') ?? '');
    },
    onmessage(ev) {
      if (isSseDoneSentinel(ev.data)) return;
      const payload: unknown = parseSseJson(ev.data);
      if (ev.event === 'started') {
        announceExecId(pickExecId(payload));
        return;
      }
      if (ev.event === 'EXEC_UPDATE') {
        useNodeExecStore.getState().applyUpdate(payload);
        if (typeof payload === 'object' && payload !== null) {
          const status = (payload as Record<string, unknown>).status;
          if (
            status === 'completed' || status === 'error' || status === 'cancelled'
          ) {
            // A node-debug run overwrites this exact workflow-scoped VFS file.
            // Invalidate after the terminal frame (the backend persists first)
            // so leaving and reopening the node reads the new snapshot.
            void queryClient.invalidateQueries({
              queryKey: [
                'vfs',
                'run-node-result',
                args.wfId,
                args.nodeId,
              ],
            });
          }
        }
        return;
      }
      if (ev.event === 'done') {
        // If a terminal frame already set completed/error, keep it; a bare
        // 'done' after a 'running'-only stream falls back to completed.
        const st = useNodeExecStore.getState().status;
        if (st === 'running') useNodeExecStore.getState().setStatus('completed');
        return;
      }
      if (ev.event === 'error') {
        const code =
          typeof payload === 'object' && payload !== null
            ? (payload as Record<string, unknown>).code
            : undefined;
        useNodeExecStore.getState().setStatus(
          code === 'cancelled' ? 'cancelled' : 'error',
        );
        return;
      }
    },
    onerror(err) {
      // No retry for a node-debug run (it's a quick, attended action) —
      // surface the failure and stop.
      useNodeExecStore.getState().setStatus('error');
      throw err;
    },
  });
}
