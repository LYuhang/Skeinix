import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { getApiBase } from '@/lib/base-path';
import { useAuthStore } from '@/stores/auth';
import type {
  SandboxLifecycleStatus,
  SandboxResourceStatus,
} from '@/lib/sandbox-status';

export interface WorkflowSandboxStatus {
  wf_id?: string;
  scope_id: string;
  mount_scope_id: string;
  status: SandboxLifecycleStatus;
  lifecycle_state?: 'warm' | 'hibernating' | 'hibernated' | 'restoring' | 'releasing' | 'snapshot_failed' | 'released' | 'closed';
  activity_state?: 'busy' | 'idle' | 'unknown';
  active_execution_ids?: string[];
  inflight_operations?: number;
  idle_elapsed_s?: number | null;
  idle_for_s?: number | null;
  ttl_phase?: 'warm_idle' | 'idle_release' | 'snapshot_retention' | null;
  ttl_s?: number | null;
  ttl_paused?: boolean;
  ttl_remaining_s?: number | null;
  next_transition?: 'hibernate' | 'warm' | 'release' | null;
  observed_at_unix_s?: number;
  closed_for_s?: number | null;
  resources?: SandboxResourceStatus;
}

export interface WorkflowSandboxStatuses {
  items: WorkflowSandboxStatus[];
}

function authHeaders(): HeadersInit | undefined {
  const token = useAuthStore.getState().token;
  return token ? { Authorization: `Bearer ${token}` } : undefined;
}

async function workflowSandboxRequest(
  wfId: string,
  method: 'GET' | 'POST' | 'DELETE',
): Promise<WorkflowSandboxStatus> {
  const base = getApiBase();
  const res = await fetch(`${base}/api/v1/workflows/${encodeURIComponent(wfId)}/sandbox`, {
    method,
    headers: authHeaders(),
  });
  if (res.status === 401) {
    useAuthStore.getState().handle401();
    throw new Error('auth');
  }
  if (!res.ok) {
    throw new Error(`workflow sandbox ${method.toLowerCase()} failed: ${res.status}`);
  }
  return (await res.json()) as WorkflowSandboxStatus;
}

async function workflowSandboxStatusesRequest(wfIds: readonly string[]): Promise<WorkflowSandboxStatuses> {
  const base = getApiBase();
  const params = new URLSearchParams();
  for (const wfId of wfIds) params.append('wf_id', wfId);
  const res = await fetch(`${base}/api/v1/workflows/sandboxes?${params.toString()}`, {
    headers: authHeaders(),
  });
  if (res.status === 401) {
    useAuthStore.getState().handle401();
    throw new Error('auth');
  }
  if (!res.ok) {
    throw new Error(`workflow sandbox statuses failed: ${res.status}`);
  }
  return (await res.json()) as WorkflowSandboxStatuses;
}

export const useWorkflowSandboxStatus = (wfId: string | undefined, enabled = true) =>
  useQuery({
    queryKey: ['workflow-sandbox', wfId],
    queryFn: () => workflowSandboxRequest(wfId as string, 'GET'),
    enabled: enabled && !!wfId,
    refetchInterval: 5000,
    refetchIntervalInBackground: false,
  });

export const useWorkflowSandboxStatuses = (wfIds: readonly string[], enabled = true) =>
  useQuery({
    queryKey: ['workflow-sandbox', 'batch', wfIds],
    queryFn: () => workflowSandboxStatusesRequest(wfIds),
    enabled: enabled && wfIds.length > 0,
    refetchInterval: 5000,
    refetchIntervalInBackground: false,
  });

export const useStartWorkflowSandbox = (wfId: string | undefined) => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => workflowSandboxRequest(wfId as string, 'POST'),
    onSuccess: (status) => {
      qc.setQueryData(['workflow-sandbox', wfId], status);
      void qc.invalidateQueries({ queryKey: ['vfs'] });
    },
  });
};

export const useCloseWorkflowSandbox = (wfId: string | undefined) => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => workflowSandboxRequest(wfId as string, 'DELETE'),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['workflow-sandbox', wfId] });
    },
  });
};
