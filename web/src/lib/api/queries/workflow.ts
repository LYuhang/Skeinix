/**
 * Single-workflow snapshot query — `GET /api/v1/workflows/{wf_id}`.
 *
 * Returns the backend's `WorkflowSnapshotOut`: `{ workflow, meta }`. The
 * `workflow` field is a flat dict keyed by `node_id` (plus a reserved
 * `__meta__`) — narrowed to a typed `NodePayload` at the call site
 * (currently `Canvas.tsx`). Strict `enabled: !!wfId` guards against a
 * transiently-undefined `useParams` value while the route mounts.
 *
 * The `['workflow', wfId]` cache key is intentionally compact so mutations
 * coming in via T7 / T12 (`POST /edits`, `POST /commits`, undo / redo /
 * checkout) can invalidate every snapshot for a workflow with a single
 * prefix invalidation without juggling version sub-keys.
 *
 * `useWorkflowAt` (T14) loads a pinned historical snapshot via
 * `GET /api/v1/workflows/{wf_id}/at/v{v}.sv{sv}`. The cache key is
 * `['workflow-at', wfId, v, sv]` — rooted under a disjoint `'workflow-at'`
 * namespace so that TanStack Query's default prefix-matched
 * `invalidateQueries({ queryKey: ['workflow', wfId] })` (fired by
 * edit/commit/undo/redo/check mutations) cannot reach these entries.
 * Historical pins are immutable on the backend and must never be
 * refetched as a side effect of mutating the latest snapshot.
 * `enabled` gates on all three values being present so callers can pass
 * `null` for `v`/`sv` while a route param is still being parsed.
 */
import { useQuery } from '@tanstack/react-query';
import { apiClient } from '@/lib/api/client';

export const useWorkflow = (wfId: string) =>
  useQuery({
    queryKey: ['workflow', wfId],
    enabled: !!wfId,
    queryFn: async () => {
      const { data, error } = await apiClient.GET('/api/v1/workflows/{wf_id}', {
        params: { path: { wf_id: wfId } },
      });
      if (error) throw error;
      return data;
    },
  });

/**
 * VFS 2c: version history for the left Explorer. The endpoint is in the
 * generated schema, so this uses the typed apiClient. The payload has NO
 * active marker — the section marks "current" by comparing (major,sub) to
 * the workflow's active_major/active_sub.
 */
export const useWorkflowVersions = (wfId: string | undefined) =>
  useQuery({
    queryKey: ['workflow', wfId, 'versions'],
    queryFn: async () => {
      const { data, error } = await apiClient.GET(
        '/api/v1/workflows/{wf_id}/versions',
        { params: { path: { wf_id: wfId as string } } },
      );
      if (error) throw error;
      return data;
    },
    enabled: !!wfId,
  });

/**
 * Query-options factory for a pinned historical snapshot. Exported so callers
 * that need a snapshot OUTSIDE the render-loop (e.g. the PromptNode prompt
 * version-diff modal, which steps across many versions imperatively) can
 * `queryClient.fetchQuery(workflowAtQuery(...))` and share the EXACT same
 * `['workflow-at', wfId, v, sv]` cache entry the hook reads — one immutable
 * fetch per (wfId, v, sv), reused everywhere.
 */
export const workflowAtQuery = (wfId: string, v: number, sv: number) => ({
  queryKey: ['workflow-at', wfId, v, sv] as const,
  queryFn: async () => {
    const { data, error } = await apiClient.GET(
      '/api/v1/workflows/{wf_id}/at/v{v}.sv{sv}',
      { params: { path: { wf_id: wfId, v, sv } } },
    );
    if (error) throw error;
    return data;
  },
});

export const useWorkflowAt = (
  wfId: string,
  v: number | null,
  sv: number | null,
) =>
  useQuery({
    // `enabled` guarantees v/sv are non-null when the queryFn runs; the `!`
    // is purely to satisfy TS narrowing across the closure boundary.
    ...workflowAtQuery(wfId, v!, sv!),
    queryKey: ['workflow-at', wfId, v, sv],
    enabled: !!wfId && v !== null && sv !== null,
  });
