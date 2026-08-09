import { fetchEventSource } from '@microsoft/fetch-event-source';
import { useEffect, useRef } from 'react';
import { useQueryClient } from '@tanstack/react-query';

import { getApiBase } from '@/lib/base-path';
import type {
  ExecutionPlanEvent,
  ExecutionPlanRun,
} from '@/lib/api/execution-plans';
import { useAuthStore } from '@/stores/auth';

interface EventSnapshot {
  items: ExecutionPlanEvent[];
  last_event_seq: number;
}

/** Tail one durable Plan Run ledger and fold it into React Query snapshots. */
export function useExecutionPlanEventStream(
  runId: string,
  initialCursor: number,
  enabled: boolean,
) {
  const queryClient = useQueryClient();
  const cursorSeedRef = useRef(initialCursor);

  useEffect(() => {
    cursorSeedRef.current = initialCursor;
  }, [initialCursor, runId]);

  useEffect(() => {
    if (!enabled || !runId) return;
    const controller = new AbortController();
    let cursor = cursorSeedRef.current;
    let refreshTimer: number | null = null;
    const touchedNodes = new Set<string>();
    const headers: Record<string, string> = { Accept: 'text/event-stream' };
    const token = useAuthStore.getState().token;
    if (token) headers.Authorization = `Bearer ${token}`;
    if (cursor > 0) headers['Last-Event-ID'] = String(cursor);

    const scheduleAuthoritativeRefresh = (nodeRunId?: string | null) => {
      if (nodeRunId) touchedNodes.add(nodeRunId);
      if (refreshTimer !== null) return;
      refreshTimer = window.setTimeout(() => {
        refreshTimer = null;
        void queryClient.invalidateQueries({ queryKey: ['execution-plan-run', runId] });
        for (const id of touchedNodes) {
          void queryClient.invalidateQueries({ queryKey: ['execution-node-run', id] });
        }
        touchedNodes.clear();
      }, 100);
    };

    void fetchEventSource(
      `${getApiBase()}/api/v1/execution-plan-runs/${encodeURIComponent(runId)}/events`,
      {
        signal: controller.signal,
        credentials: 'include',
        openWhenHidden: true,
        headers,
        onopen: async (response) => {
          if (response.status === 401) {
            useAuthStore.getState().handle401();
            throw new Error('auth');
          }
          if (!response.ok) throw new Error(`execution plan stream open failed: ${response.status}`);
        },
        onmessage(message) {
          if (message.event !== 'execution_plan' || !message.data) return;
          try {
            const event = JSON.parse(message.data) as ExecutionPlanEvent;
            if (!Number.isFinite(event.seq) || event.seq <= cursor) return;
            cursor = event.seq;
            headers['Last-Event-ID'] = String(cursor);
            queryClient.setQueryData<EventSnapshot>(
              ['execution-plan-events', runId],
              (current) => {
                const prior = current?.items ?? [];
                const items = prior.some((item) => item.seq === event.seq)
                  ? prior
                  : [...prior, event].sort((a, b) => a.seq - b.seq).slice(-500);
                return { items, last_event_seq: cursor };
              },
            );
            queryClient.setQueryData<ExecutionPlanRun>(
              ['execution-plan-run', runId],
              (current) => {
                if (!current) return current;
                const eventStatus = typeof event.payload.status === 'string'
                  ? event.payload.status
                  : null;
                const nodes = event.node_run_id
                  ? current.nodes.map((node) => node.node_run_id === event.node_run_id
                    ? {
                        ...node,
                        ...(eventStatus ? { status: eventStatus as typeof node.status } : {}),
                        current_activity: typeof event.payload.progress === 'object'
                          && event.payload.progress
                          && 'message' in event.payload.progress
                          ? String((event.payload.progress as { message?: unknown }).message ?? node.current_activity)
                          : node.current_activity,
                      }
                    : node)
                  : current.nodes;
                return {
                  ...current,
                  nodes,
                  last_event_seq: Math.max(current.last_event_seq, event.seq),
                  ...(eventStatus && event.event_type.startsWith('run_')
                    ? { status: eventStatus as ExecutionPlanRun['status'] }
                    : {}),
                };
              },
            );
            scheduleAuthoritativeRefresh(event.node_run_id);
          } catch {
            scheduleAuthoritativeRefresh();
          }
        },
        onclose() {
          if (!controller.signal.aborted) throw new Error('execution plan stream closed');
        },
        onerror(error) {
          if (controller.signal.aborted) throw error;
        },
      },
    ).catch(() => {
      // Durable snapshot queries remain authoritative across reconnects.
    });

    return () => {
      controller.abort();
      if (refreshTimer !== null) window.clearTimeout(refreshTimer);
    };
  }, [enabled, queryClient, runId]);
}
