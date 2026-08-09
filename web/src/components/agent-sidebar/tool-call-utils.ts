import type { MergedToolCall } from './types';

export interface ExecutionPlanToolTarget {
  planId: string;
  runId: string;
  revision?: number;
}

function record(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

export function workflowIdFromToolCall(call: MergedToolCall): string | null {
  const artifact = call.artifact;
  const nested = artifact?.artifact;
  const handles = nested && typeof nested === 'object' && !Array.isArray(nested)
    ? (nested as { handles?: unknown }).handles
    : null;
  if (handles && typeof handles === 'object' && !Array.isArray(handles)) {
    const id = (handles as { workflow_id?: unknown }).workflow_id;
    if (typeof id === 'string' && id) return id;
  }
  const payload = artifact?.payload;
  if (payload && typeof payload === 'object' && !Array.isArray(payload)) {
    const id = (payload as { workflow_id?: unknown }).workflow_id;
    if (typeof id === 'string' && id) return id;
  }
  try {
    const parsed = call.result ? JSON.parse(call.result) : null;
    if (parsed && typeof parsed === 'object') {
      const value = parsed as { workflow_id?: unknown; meta?: { workflow_id?: unknown } };
      if (typeof value.workflow_id === 'string') return value.workflow_id;
      if (typeof value.meta?.workflow_id === 'string') return value.meta.workflow_id;
    }
  } catch {
    // Legacy plain-text results carry no workflow id.
  }
  return null;
}

export function executionPlanFromToolCall(
  call: MergedToolCall,
): ExecutionPlanToolTarget | null {
  if (call.name !== 'create_execution_plan') return null;
  const artifact = record(call.artifact);
  const artifactBody = record(artifact?.artifact);
  const handles = record(artifactBody?.handles);
  const plan = record(handles?.execution_plan);
  const planId = plan?.plan_id;
  const runId = plan?.plan_run_id;
  if (typeof planId !== 'string' || !planId || typeof runId !== 'string' || !runId) {
    return null;
  }
  return {
    planId,
    runId,
    revision: typeof plan.revision === 'number' ? plan.revision : undefined,
  };
}
