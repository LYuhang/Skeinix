import { getApiBase } from '@/lib/base-path';

export type ExecutionPlanStatus =
  | 'awaiting_approval' | 'queued' | 'running' | 'completed' | 'failed'
  | 'cancel_requested' | 'cancelled' | 'not_started';

export type ExecutionNodeStatus =
  | 'pending' | 'ready' | 'queued' | 'running' | 'succeeded' | 'failed'
  | 'cancel_requested' | 'cancelled' | 'skipped';

export interface ExecutionPlanCard {
  plan_id: string;
  plan_run_id: string;
  job_id: string;
  chat_id: string;
  revision: number;
  title: string;
  status: ExecutionPlanStatus;
  node_count: number;
  parallel_branch_count: number;
  progress: Record<string, unknown>;
  last_event_seq: number;
  created_at?: string | null;
  updated_at?: string | null;
  approval?: {
    hitl_request_id: string;
    status: string;
    title: string;
    prompt_text: string;
    tool_name?: string | null;
  } | null;
}

export interface ExecutionPlanDefinitionNode {
  id: string;
  type: 'start' | 'subagent' | 'end';
  title?: string;
  task?: string;
  next?: string[];
}

export interface ExecutionPlanDetail {
  plan_id: string;
  chat_id: string;
  revision: number;
  lifecycle_status: string;
  definition: {
    schema_version: 1;
    title: string;
    nodes: ExecutionPlanDefinitionNode[];
    budgets: Record<string, number>;
  };
  validation: Record<string, unknown>;
  source_plan_path: string;
  definition_hash: string;
  created_at?: string | null;
  runs: Array<Record<string, unknown>>;
}

export interface ExecutionNodeRun {
  node_run_id: string;
  node_path: string;
  node_type: ExecutionPlanDefinitionNode['type'];
  status: ExecutionNodeStatus;
  attention_status: string;
  current_attempt: number;
  current_activity: string;
  definition: ExecutionPlanDefinitionNode;
  result?: unknown;
  output_ref?: string | null;
  error: Record<string, unknown>;
  side_effect_state: string;
  progress: { current?: number; total?: number | null };
  cancel_requested: boolean;
  approval?: {
    hitl_request_id: string;
    status: string;
    title: string;
    prompt_text: string;
    tool_name?: string | null;
  } | null;
  started_at?: string | null;
  ended_at?: string | null;
  updated_at?: string | null;
}

export interface ExecutionPlanRun {
  plan_run_id: string;
  job_id: string;
  plan_id: string;
  revision: number;
  chat_id: string;
  status: ExecutionPlanStatus;
  approval_mode: string;
  budget: Record<string, number>;
  progress: Record<string, unknown>;
  last_event_seq: number;
  cancel_requested: boolean;
  started_at?: string | null;
  ended_at?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  nodes: ExecutionNodeRun[];
  approval?: { hitl_request_id: string; status: string } | null;
}

export interface ExecutionNodeDetail extends ExecutionNodeRun {
  plan_run_id: string;
  chat_id: string;
  attempts: Array<Record<string, unknown>>;
  output: Array<{
    seq: number;
    kind: string;
    content_type: string;
    payload: Record<string, unknown>;
    created_at?: string | null;
  }>;
}

export interface ExecutionPlanEvent {
  seq: number;
  event_type: string;
  node_run_id?: string | null;
  attempt?: number | null;
  payload: Record<string, unknown>;
  trace_ref?: string | null;
  created_at?: string | null;
}

const BASE = getApiBase();

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const { useAuthStore } = await import('@/stores/auth');
  const headers = new Headers(init.headers);
  const token = useAuthStore.getState().token;
  if (token) headers.set('Authorization', `Bearer ${token}`);
  if (init.body) headers.set('Content-Type', 'application/json');
  const response = await fetch(`${BASE}${path}`, { ...init, headers });
  if (response.status === 401) useAuthStore.getState().handle401();
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || `Execution plan request failed (${response.status})`);
  }
  return response.json() as Promise<T>;
}

export const listExecutionPlans = (chatId: string) =>
  request<ExecutionPlanCard[]>(`/api/v1/execution-plans?chat_id=${encodeURIComponent(chatId)}`);

export const getExecutionPlan = (planId: string, revision?: number) =>
  request<ExecutionPlanDetail>(`/api/v1/execution-plans/${encodeURIComponent(planId)}${revision ? `?revision=${revision}` : ''}`);

export const getExecutionPlanRun = (runId: string) =>
  request<ExecutionPlanRun>(`/api/v1/execution-plan-runs/${encodeURIComponent(runId)}`);

export const getExecutionNodeRun = (nodeRunId: string) =>
  request<ExecutionNodeDetail>(`/api/v1/execution-node-runs/${encodeURIComponent(nodeRunId)}`);

export const getExecutionPlanEvents = (runId: string) =>
  request<{ items: ExecutionPlanEvent[]; last_event_seq: number }>(
    `/api/v1/execution-plan-runs/${encodeURIComponent(runId)}/events/snapshot`,
  );

export const decideExecutionPlanStart = (hitlRequestId: string, decision: 'approve' | 'deny') =>
  request<Record<string, unknown>>(`/api/v1/hitl-requests/${encodeURIComponent(hitlRequestId)}/decision`, {
    method: 'POST', body: JSON.stringify({ decision, decision_payload: {}, interaction_result: {} }),
  });

function controlId(prefix: string): string {
  return `${prefix}:${crypto.randomUUID()}`;
}

export const cancelExecutionPlanRun = (runId: string) =>
  request<ExecutionPlanRun>(`/api/v1/execution-plan-runs/${encodeURIComponent(runId)}/cancel`, {
    method: 'POST', body: JSON.stringify({ idempotency_key: controlId('cancel-run'), reason: 'user_requested' }),
  });

export const cancelExecutionNodeRun = (nodeRunId: string) =>
  request<ExecutionNodeDetail>(`/api/v1/execution-node-runs/${encodeURIComponent(nodeRunId)}/cancel`, {
    method: 'POST', body: JSON.stringify({ idempotency_key: controlId('cancel-node'), reason: 'user_requested' }),
  });
