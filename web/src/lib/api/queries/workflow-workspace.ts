import { useQuery } from '@tanstack/react-query';
import { getApiBase } from '@/lib/base-path';
import { useAuthStore } from '@/stores/auth';

export interface WorkflowWorkspaceIdentity {
  workflow_scope_id: string;
  mount_scope_id: string;
}

async function readWorkflowWorkspaceIdentity(wfId: string): Promise<WorkflowWorkspaceIdentity> {
  const base = getApiBase();
  const token = useAuthStore.getState().token;
  const response = await fetch(
    `${base}/api/v1/workflows/${encodeURIComponent(wfId)}/workspace`,
    { headers: token ? { Authorization: `Bearer ${token}` } : undefined },
  );
  if (response.status === 401) useAuthStore.getState().handle401();
  if (!response.ok) throw new Error(`workflow workspace failed: ${response.status}`);
  return (await response.json()) as WorkflowWorkspaceIdentity;
}

export const useWorkflowWorkspaceIdentity = (wfId: string | undefined, enabled = true) =>
  useQuery({
    queryKey: ['workflow-workspace', wfId],
    queryFn: () => readWorkflowWorkspaceIdentity(wfId as string),
    enabled: enabled && !!wfId,
    staleTime: Infinity,
  });
