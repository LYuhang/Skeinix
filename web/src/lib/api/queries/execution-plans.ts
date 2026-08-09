import { useQuery } from '@tanstack/react-query';
import {
  getExecutionNodeRun,
  getExecutionPlan,
  getExecutionPlanRun,
  getExecutionPlanEvents,
  listExecutionPlans,
} from '@/lib/api/execution-plans';

export const useExecutionPlans = (chatId: string | null) => useQuery({
  queryKey: ['execution-plans', chatId],
  enabled: Boolean(chatId),
  queryFn: () => listExecutionPlans(chatId!),
  refetchInterval: 2_000,
});

export const useExecutionPlan = (planId: string, revision?: number) => useQuery({
  queryKey: ['execution-plan', planId, revision],
  queryFn: () => getExecutionPlan(planId, revision),
  staleTime: Infinity,
});

export const useExecutionPlanRun = (runId: string) => useQuery({
  queryKey: ['execution-plan-run', runId],
  queryFn: () => getExecutionPlanRun(runId),
  refetchInterval: (query) => {
    const status = query.state.data?.status;
    return status && ['completed', 'failed', 'cancelled', 'not_started'].includes(status) ? false : 1_000;
  },
});

export const useExecutionNodeRun = (nodeRunId: string | null) => useQuery({
  queryKey: ['execution-node-run', nodeRunId],
  enabled: Boolean(nodeRunId),
  queryFn: () => getExecutionNodeRun(nodeRunId!),
  refetchInterval: (query) => query.state.data?.status === 'running' ? 1_000 : 2_500,
});

export const useExecutionPlanEvents = (runId: string) => useQuery({
  queryKey: ['execution-plan-events', runId],
  queryFn: () => getExecutionPlanEvents(runId),
  refetchInterval: 10_000,
});
