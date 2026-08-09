import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import {
  CheckCircle2,
  GitBranch,
  LayoutDashboard,
  List,
  Play,
  Rows3,
  Square,
} from 'lucide-react';

import { AsyncState } from '@/components/ui/async-state';
import { Button } from '@/components/ui/button';
import { ConfirmationDialog } from '@/components/ui/confirmation-dialog';
import { StatusBadge, type SemanticStatus } from '@/components/ui/status';
import {
  cancelExecutionNodeRun,
  cancelExecutionPlanRun,
  decideExecutionPlanStart,
  type ExecutionNodeRun,
  type ExecutionPlanStatus,
} from '@/lib/api/execution-plans';
import {
  useExecutionNodeRun,
  useExecutionPlan,
  useExecutionPlanEvents,
  useExecutionPlanRun,
} from '@/lib/api/queries/execution-plans';
import { useExecutionPlanEventStream } from '@/lib/api/sse/execution-plan-events';
import { cn } from '@/lib/utils';
import { ExecutionNodeInspector } from './ExecutionNodeInspector';
import { ExecutionPlanGraph } from './ExecutionPlanGraph';

type View = 'graph' | 'table' | 'activity';

function tone(status: ExecutionPlanStatus | ExecutionNodeRun['status']): SemanticStatus {
  if (status === 'completed' || status === 'succeeded') return 'success';
  if (status === 'failed') return 'danger';
  if (status === 'awaiting_approval' || status === 'cancel_requested') return 'warning';
  if (status === 'running' || status === 'queued') return 'running';
  return 'neutral';
}

function duration(start?: string | null, end?: string | null): string {
  if (!start) return 'Not started';
  const seconds = Math.max(0, Math.floor((new Date(end ?? Date.now()).getTime() - new Date(start).getTime()) / 1000));
  if (seconds < 60) return `${seconds}s`;
  return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
}

function initialSelectionFromUrl(runId: string): string | null {
  if (typeof window === 'undefined') return null;
  const params = new URLSearchParams(window.location.search);
  return params.get('run') === runId ? params.get('node') : null;
}

export function ExecutionPlanPreview({
  planId,
  runId,
  revision,
}: {
  planId: string;
  runId: string;
  revision?: number;
}) {
  const plan = useExecutionPlan(planId, revision);
  const run = useExecutionPlanRun(runId);
  const events = useExecutionPlanEvents(runId);
  const runTerminal = Boolean(run.data && ['completed', 'failed', 'cancelled', 'not_started'].includes(run.data.status));
  useExecutionPlanEventStream(
    runId,
    Math.max(run.data?.last_event_seq ?? 0, events.data?.last_event_seq ?? 0),
    Boolean(run.data) && !runTerminal,
  );
  const queryClient = useQueryClient();
  const [view, setView] = useState<View>('graph');
  const [selectedPath, setSelectedPath] = useState<string | null | undefined>(
    () => initialSelectionFromUrl(runId) ?? undefined,
  );
  const [inspectorTabs, setInspectorTabs] = useState<Record<string, 'configuration' | 'output'>>({});
  const [confirm, setConfirm] = useState<'run' | 'node' | null>(null);

  const defaultSelectedPath = run.data?.nodes.find((node) => node.status === 'running')?.node_path
    ?? run.data?.nodes[0]?.node_path
    ?? null;
  const activeSelectedPath = selectedPath === undefined ? defaultSelectedPath : selectedPath;
  const selected = run.data?.nodes.find((node) => node.node_path === activeSelectedPath) ?? null;
  const nodeDetail = useExecutionNodeRun(selected?.node_run_id ?? null);

  useEffect(() => {
    if (!activeSelectedPath || typeof window === 'undefined') return;
    const url = new URL(window.location.href);
    url.searchParams.set('preview', `plan:${planId}`);
    url.searchParams.set('run', runId);
    url.searchParams.set('node', activeSelectedPath);
    window.history.replaceState(window.history.state, '', url);
  }, [activeSelectedPath, planId, runId]);

  const invalidate = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['execution-plan-run', runId] }),
      queryClient.invalidateQueries({ queryKey: ['execution-plan-events', runId] }),
      selected?.node_run_id
        ? queryClient.invalidateQueries({ queryKey: ['execution-node-run', selected.node_run_id] })
        : Promise.resolve(),
    ]);
  };
  const approval = useMutation({
    mutationFn: (decision: 'approve' | 'deny') => decideExecutionPlanStart(run.data!.approval!.hitl_request_id, decision),
    onSuccess: invalidate,
  });
  const cancelRun = useMutation({
    mutationFn: () => cancelExecutionPlanRun(runId),
    onSuccess: async () => { setConfirm(null); await invalidate(); },
  });
  const cancelNode = useMutation({
    mutationFn: () => cancelExecutionNodeRun(selected!.node_run_id),
    onSuccess: async () => { setConfirm(null); await invalidate(); },
  });
  const nodeApproval = useMutation({
    mutationFn: (decision: 'approve' | 'deny') => decideExecutionPlanStart(
      nodeDetail.data!.approval!.hitl_request_id,
      decision,
    ),
    onSuccess: invalidate,
  });

  const counts = useMemo(() => {
    const nodes = run.data?.nodes ?? [];
    return {
      done: nodes.filter((node) => node.status === 'succeeded').length,
      active: nodes.filter((node) => ['ready', 'queued', 'running'].includes(node.status)).length,
      issues: nodes.filter((node) => ['failed', 'cancelled', 'skipped'].includes(node.status)).length,
    };
  }, [run.data?.nodes]);

  if (plan.isLoading || run.isLoading) {
    return <AsyncState kind="loading" title="Loading execution plan…" className="h-full rounded-none border-0" />;
  }
  if (plan.isError || run.isError || !plan.data || !run.data) {
    return <AsyncState kind="error" title="Unable to load this execution plan" actionLabel="Refresh" onAction={() => void Promise.all([plan.refetch(), run.refetch()])} className="h-full rounded-none border-0" />;
  }

  const definitions = plan.data.definition.nodes;
  const tab = selected
    ? inspectorTabs[selected.node_path] ?? (['pending', 'ready'].includes(selected.status) ? 'configuration' : 'output')
    : 'configuration';
  const primary = run.data.status === 'awaiting_approval'
    ? { label: 'Approve & Start', icon: Play, action: () => approval.mutate('approve'), disabled: approval.isPending }
    : ['queued', 'running', 'cancel_requested'].includes(run.data.status)
      ? { label: run.data.status === 'cancel_requested' ? 'Cancelling…' : 'Cancel run', icon: Square, action: () => setConfirm('run'), disabled: run.data.status === 'cancel_requested' }
      : { label: 'View result', icon: CheckCircle2, action: () => {
          const end = run.data.nodes.find((node) => node.node_type === 'end') ?? run.data.nodes.at(-1);
          if (end) { setSelectedPath(end.node_path); setInspectorTabs((current) => ({ ...current, [end.node_path]: 'output' })); }
        }, disabled: false };
  const PrimaryIcon = primary.icon;

  return (
    <div className="relative flex h-full min-h-0 flex-col bg-surface-work">
      <header className="shrink-0 border-b border-edge-subtle bg-surface-raised px-4 py-3">
        <div className="flex flex-wrap items-center gap-3">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <h2 className="truncate text-base font-semibold">{plan.data.definition.title}</h2>
              <span className="rounded bg-surface-sunken px-1.5 py-0.5 text-xs text-content-tertiary">rev {plan.data.revision}</span>
              <StatusBadge status={tone(run.data.status)}>{run.data.status.replace('_', ' ')}</StatusBadge>
            </div>
            <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
              <span>{definitions.length} nodes</span>
              <span>{counts.done} completed · {counts.active} active · {counts.issues} other</span>
              <span className="tabular-nums">{duration(run.data.started_at, run.data.ended_at)}</span>
            </div>
          </div>
          {run.data.status === 'awaiting_approval' ? (
            <Button variant="ghost" size="sm" disabled={approval.isPending} onClick={() => approval.mutate('deny')}>Don't start</Button>
          ) : null}
          <Button onClick={primary.action} disabled={primary.disabled} className="gap-2">
            <PrimaryIcon className={cn('h-4 w-4', primary.disabled && run.data.status === 'cancel_requested' && 'motion-safe:animate-pulse')} />
            {primary.label}
          </Button>
        </div>
        {run.data.status === 'awaiting_approval' ? (
          <div className="mt-3 rounded-md border border-state-warning/30 bg-state-warning/5 px-3 py-2 text-xs leading-5 text-muted-foreground">
            Review the immutable graph and its budget before starting. Approving the plan does not automatically approve dangerous tools inside a subagent.
          </div>
        ) : null}
      </header>

      <div className="flex min-h-0 flex-1">
        <main className="flex min-w-0 flex-1 flex-col">
          <div className="flex h-10 shrink-0 items-center gap-1 border-b border-edge-subtle px-3" aria-label="Plan view">
            {([
              ['graph', LayoutDashboard, 'Graph'],
              ['table', Rows3, 'Table'],
              ['activity', List, 'Activity'],
            ] as const).map(([value, Icon, label]) => (
              <Button key={value} variant={view === value ? 'secondary' : 'ghost'} size="sm" className="h-8 gap-1.5" onClick={() => setView(value)} aria-pressed={view === value}>
                <Icon className="h-3.5 w-3.5" /> {label}
              </Button>
            ))}
          </div>
          <div className="min-h-0 flex-1">
            {view === 'graph' ? (
              <ExecutionPlanGraph definitions={definitions} runs={run.data.nodes} selectedNodePath={activeSelectedPath} onSelect={setSelectedPath} />
            ) : view === 'table' ? (
              <div className="app-scrollbar h-full overflow-auto p-3">
                <table className="w-full min-w-[640px] border-separate border-spacing-0 text-sm">
                  <thead className="sticky top-0 z-10 bg-surface-work text-left text-xs text-content-tertiary">
                    <tr><th className="border-b border-edge-subtle p-2">Node</th><th className="border-b border-edge-subtle p-2">Type</th><th className="border-b border-edge-subtle p-2">Status</th><th className="border-b border-edge-subtle p-2">Current activity</th><th className="border-b border-edge-subtle p-2">Duration</th></tr>
                  </thead>
                  <tbody>{run.data.nodes.map((node) => (
                    <tr key={node.node_run_id} className={cn('cursor-pointer hover:bg-surface-hover', activeSelectedPath === node.node_path && 'bg-surface-selected')} onClick={() => setSelectedPath(node.node_path)}>
                      <td className="border-b border-edge-subtle p-2 font-medium">{node.definition.title || node.node_path}</td>
                      <td className="border-b border-edge-subtle p-2 text-muted-foreground">{node.node_type}</td>
                      <td className="border-b border-edge-subtle p-2"><StatusBadge status={tone(node.status)}>{node.status.replace('_', ' ')}</StatusBadge></td>
                      <td className="max-w-[320px] truncate border-b border-edge-subtle p-2 text-muted-foreground">{node.current_activity || '—'}</td>
                      <td className="border-b border-edge-subtle p-2 tabular-nums text-muted-foreground">{duration(node.started_at, node.ended_at)}</td>
                    </tr>
                  ))}</tbody>
                </table>
              </div>
            ) : (
              <div className="app-scrollbar h-full overflow-auto p-4">
                <ol className="mx-auto max-w-3xl space-y-1">
                  {(events.data?.items ?? []).map((event) => {
                    const node = run.data.nodes.find((item) => item.node_run_id === event.node_run_id);
                    return (
                      <li key={event.seq} style={(events.data?.items.length ?? 0) > 50 ? { contentVisibility: 'auto', containIntrinsicSize: '0 48px' } : undefined}>
                        <button type="button" className="flex w-full gap-3 rounded-md px-3 py-2 text-left hover:bg-surface-hover" onClick={() => node && setSelectedPath(node.node_path)}>
                          <span className="mt-1 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-surface-sunken"><GitBranch className="h-3.5 w-3.5" /></span>
                          <span className="min-w-0 flex-1"><span className="block text-sm font-medium">{event.event_type.replaceAll('_', ' ')}</span><span className="block truncate text-xs text-muted-foreground">{node?.definition.title || String(event.payload.status ?? 'Plan event')}</span></span>
                          <span className="text-xs tabular-nums text-content-tertiary">#{event.seq}</span>
                        </button>
                      </li>
                    );
                  })}
                  {!events.data?.items.length ? <li className="py-12 text-center text-sm text-muted-foreground">No activity has been recorded yet.</li> : null}
                </ol>
              </div>
            )}
          </div>
        </main>

        {selected ? (
          <ExecutionNodeInspector
            node={selected}
            detail={nodeDetail.data}
            tab={tab}
            onTabChange={(next) => setInspectorTabs((current) => ({ ...current, [selected.node_path]: next }))}
            onClose={() => setSelectedPath(null)}
            onCancel={() => setConfirm('node')}
            onApproval={(decision) => nodeApproval.mutate(decision)}
            approvalPending={nodeApproval.isPending}
          />
        ) : null}
      </div>

      <ConfirmationDialog
        open={confirm === 'run'}
        onOpenChange={(open) => !open && setConfirm(null)}
        title="Cancel this run?"
        description="Running subagents will be asked to stop. Completed output and artifacts remain available; dependent nodes that cannot run will be skipped."
        confirmLabel="Cancel run"
        cancelLabel="Keep running"
        onConfirm={() => cancelRun.mutate()}
        pending={cancelRun.isPending}
      />
      <ConfirmationDialog
        open={confirm === 'node'}
        onOpenChange={(open) => !open && setConfirm(null)}
        title={`Cancel ${selected?.definition.title || selected?.node_path || 'this node'}?`}
        description="This node will stop and keep any committed partial output. Downstream nodes that require its output may be skipped; independent parallel work can continue."
        confirmLabel="Cancel node"
        cancelLabel="Keep running"
        onConfirm={() => cancelNode.mutate()}
        pending={cancelNode.isPending}
      />
    </div>
  );
}
