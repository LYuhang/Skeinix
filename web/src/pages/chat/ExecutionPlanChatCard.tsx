import { useMutation, useQueryClient } from '@tanstack/react-query';
import { CheckCircle2, GitBranch, LoaderCircle, Play, XCircle } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { StatusBadge, type SemanticStatus } from '@/components/ui/status';
import {
  decideExecutionPlanStart,
  type ExecutionPlanCard,
} from '@/lib/api/execution-plans';
import { useExecutionPlanRun } from '@/lib/api/queries/execution-plans';

function tone(status: ExecutionPlanCard['status']): SemanticStatus {
  if (status === 'completed') return 'success';
  if (status === 'failed') return 'danger';
  if (status === 'awaiting_approval' || status === 'cancel_requested') return 'warning';
  if (status === 'queued' || status === 'running') return 'running';
  return 'neutral';
}

export function ExecutionPlanChatCard({
  plan,
  onOpen,
}: {
  plan: ExecutionPlanCard;
  onOpen: () => void;
}) {
  const run = useExecutionPlanRun(plan.plan_run_id);
  const queryClient = useQueryClient();
  const approve = useMutation({
    mutationFn: () => decideExecutionPlanStart(run.data!.approval!.hitl_request_id, 'approve'),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['execution-plan-run', plan.plan_run_id] }),
        queryClient.invalidateQueries({ queryKey: ['execution-plans', plan.chat_id] }),
        queryClient.invalidateQueries({ queryKey: ['hitl-requests', plan.chat_id] }),
      ]);
    },
  });
  const status = run.data?.status ?? plan.status;
  const completed = Number(run.data?.progress.completed_nodes ?? plan.progress.completed_nodes ?? 0);
  const total = Number(run.data?.progress.total_nodes ?? plan.progress.total_nodes ?? plan.node_count);
  return (
    <section
      className="mb-2 overflow-hidden rounded-lg border border-edge-subtle bg-surface-raised shadow-sm"
      aria-label={`Execution plan: ${plan.title}`}
      data-role="execution-plan-card"
      data-plan-status={status}
    >
      <div className="flex items-center gap-3 px-3 py-2.5">
        <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-surface-sunken text-muted-foreground">
          <GitBranch className="h-4 w-4" />
        </span>
        <button type="button" className="min-w-0 flex-1 text-left" onClick={onOpen}>
          <span className="flex items-center gap-2">
            <span className="truncate text-sm font-medium">{plan.title}</span>
            <StatusBadge status={tone(status)}>{status.replace('_', ' ')}</StatusBadge>
          </span>
          <span className="mt-0.5 block text-xs text-muted-foreground">
            {completed}/{total} nodes · revision {plan.revision}
          </span>
        </button>
        {status === 'awaiting_approval' && run.data?.approval?.status === 'pending' ? (
          <Button size="sm" className="gap-1.5" disabled={approve.isPending} onClick={() => approve.mutate()}>
            {approve.isPending ? <LoaderCircle className="h-3.5 w-3.5 motion-safe:animate-spin" /> : <Play className="h-3.5 w-3.5" />}
            Approve & Start
          </Button>
        ) : status === 'completed' ? (
          <CheckCircle2 className="h-4 w-4 text-state-success" aria-label="Completed" />
        ) : status === 'failed' ? (
          <XCircle className="h-4 w-4 text-state-danger" aria-label="Failed" />
        ) : null}
        <Button variant="ghost" size="sm" onClick={onOpen}>Review plan</Button>
      </div>
    </section>
  );
}
