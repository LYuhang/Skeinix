import { useCallback, useEffect, useRef, useState } from 'react';
import { AlertTriangle, Bot, ChevronDown, Clock3, Square, Wrench, X } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { StatusBadge, type SemanticStatus } from '@/components/ui/status';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import type { ExecutionNodeDetail, ExecutionNodeRun } from '@/lib/api/execution-plans';
import { ToolArgumentsView } from '@/components/agent-sidebar/tool-render/ToolArgumentsView';

function tone(status: string): SemanticStatus {
  if (status === 'succeeded') return 'success';
  if (status === 'failed') return 'danger';
  if (status === 'running') return 'running';
  if (status === 'cancel_requested') return 'warning';
  return 'neutral';
}

function Scalar({ value }: { value: unknown }) {
  if (value === null || value === undefined || value === '') return <span className="text-content-tertiary">—</span>;
  if (typeof value === 'boolean') return <span>{value ? 'Yes' : 'No'}</span>;
  if (typeof value === 'string' || typeof value === 'number') return <span className="break-words">{String(value)}</span>;
  if (Array.isArray(value)) {
    return <ul className="space-y-1">{value.map((item, index) => <li key={index}><Scalar value={item} /></li>)}</ul>;
  }
  if (typeof value === 'object') {
    return (
      <dl className="space-y-1.5">
        {Object.entries(value as Record<string, unknown>).map(([key, item]) => (
          <div key={key} className="grid grid-cols-[minmax(88px,0.35fr)_minmax(0,1fr)] gap-2">
            <dt className="text-content-tertiary">{key.replaceAll('_', ' ')}</dt>
            <dd><Scalar value={item} /></dd>
          </div>
        ))}
      </dl>
    );
  }
  return <span>{String(value)}</span>;
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="border-b border-edge-subtle py-4 last:border-0">
      <h4 className="mb-3 text-xs font-semibold uppercase tracking-wide text-content-tertiary">{title}</h4>
      {children}
    </section>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[112px_minmax(0,1fr)] gap-3 py-1.5 text-sm max-[460px]:grid-cols-1 max-[460px]:gap-1">
      <div className="text-muted-foreground">{label}</div>
      <div className="min-w-0 text-foreground">{children}</div>
    </div>
  );
}

function Configuration({ node }: { node: ExecutionNodeRun }) {
  const definition = node.definition;
  return (
    <div className="px-4">
      <Section title="Task">
        <p className="whitespace-pre-wrap text-sm leading-6 text-foreground">
          {definition.task || definition.title || `${definition.type} control node`}
        </p>
      </Section>
      <Section title="Handoff contract">
        <p className="text-sm leading-6 text-muted-foreground">
          This is a static task. Required facts and any fixed VFS paths for upstream or downstream handoff are declared directly in the task prompt.
        </p>
      </Section>
      <Section title="Control flow">
        {definition.next?.map((target, index) => (
          <Field key={target} label={index === 0 ? 'Next' : ''}>
            <span className="font-mono text-xs">→ {target}</span>
            {(definition.next?.length ?? 0) > 1 ? <span className="ml-2 text-xs text-muted-foreground">parallel branch</span> : null}
          </Field>
        ))}
        {!definition.next?.length ? <p className="text-sm text-muted-foreground">Terminal node.</p> : null}
      </Section>
      <Section title="Execution policy">
        <Field label="Runtime">LangChain detached subagent</Field>
      </Section>
    </div>
  );
}

interface OutputScrollState { top: number; follow: boolean }

function PublicTraceEntry({ payload }: { payload: Record<string, unknown> }) {
  const text = typeof payload.text === 'string' ? payload.text : '';
  const calls = Array.isArray(payload.tool_calls)
    ? payload.tool_calls.filter((item): item is Record<string, unknown> => Boolean(item && typeof item === 'object'))
    : [];
  return (
    <div className="space-y-2">
      {text ? <p className="whitespace-pre-wrap break-words">{text}</p> : null}
      {calls.map((call, index) => {
        const name = typeof call.name === 'string' && call.name ? call.name : 'Tool';
        return (
          <details key={`${name}:${index}`} className="overflow-hidden rounded-md border border-edge-subtle bg-surface-raised">
            <summary className="flex cursor-pointer list-none items-center gap-2 px-3 py-2 font-medium hover:bg-surface-hover">
              <Wrench className="h-3.5 w-3.5 text-content-tertiary" />
              <span className="min-w-0 flex-1 truncate">{name.replaceAll('_', ' ')}</span>
              <ChevronDown className="h-3.5 w-3.5 text-content-tertiary" />
            </summary>
            <div className="border-t border-edge-subtle p-2">
              <ToolArgumentsView toolName={name} argumentsText={JSON.stringify(call.args ?? {})} />
            </div>
          </details>
        );
      })}
      {!text && !calls.length ? <Scalar value={payload} /> : null}
    </div>
  );
}

function Output({
  node,
  detail,
  getScrollState,
  setScrollState,
  onApproval,
  approvalPending,
}: {
  node: ExecutionNodeRun;
  detail?: ExecutionNodeDetail;
  getScrollState: (nodeRunId: string) => OutputScrollState | undefined;
  setScrollState: (nodeRunId: string, state: OutputScrollState) => void;
  onApproval: (decision: 'approve' | 'deny') => void;
  approvalPending: boolean;
}) {
  const chunks = (detail?.output ?? []).filter((chunk) => chunk.kind !== 'result');
  const viewportRef = useRef<HTMLDivElement>(null);
  const resultRef = useRef<HTMLDivElement>(null);
  const previousCount = useRef(chunks.length);
  const previousStatus = useRef(node.status);
  const [newUpdates, setNewUpdates] = useState(0);
  const [resultReady, setResultReady] = useState(false);

  useEffect(() => {
    const viewport = viewportRef.current;
    if (!viewport) return;
    const saved = getScrollState(node.node_run_id) ?? { top: 0, follow: true };
    viewport.scrollTop = saved.top;
    if (saved.follow) viewport.scrollTop = viewport.scrollHeight;
  }, [getScrollState, node.node_run_id]);

  useEffect(() => {
    const added = Math.max(0, chunks.length - previousCount.current);
    previousCount.current = chunks.length;
    if (!added) return;
    const viewport = viewportRef.current;
    const saved = getScrollState(node.node_run_id) ?? { top: 0, follow: true };
    if (saved.follow && viewport) {
      requestAnimationFrame(() => { viewport.scrollTop = viewport.scrollHeight; });
      setNewUpdates(0);
    } else {
      setNewUpdates((value) => value + added);
    }
  }, [chunks.length, getScrollState, node.node_run_id]);

  useEffect(() => {
    const wasTerminal = ['succeeded', 'failed', 'cancelled', 'skipped'].includes(previousStatus.current);
    const isTerminal = ['succeeded', 'failed', 'cancelled', 'skipped'].includes(node.status);
    previousStatus.current = node.status;
    if (!wasTerminal && isTerminal && (viewportRef.current?.scrollTop ?? 0) > 80) {
      setResultReady(true);
    }
  }, [node.status]);

  const onScroll = () => {
    const viewport = viewportRef.current;
    if (!viewport) return;
    const follow = viewport.scrollHeight - viewport.scrollTop - viewport.clientHeight < 48;
    setScrollState(node.node_run_id, { top: viewport.scrollTop, follow });
    if (follow) setNewUpdates(0);
  };

  const jumpToLatest = () => {
    const viewport = viewportRef.current;
    if (!viewport) return;
    viewport.scrollTo({ top: viewport.scrollHeight, behavior: 'smooth' });
    setScrollState(node.node_run_id, { top: viewport.scrollHeight, follow: true });
    setNewUpdates(0);
  };

  return (
    <div ref={viewportRef} onScroll={onScroll} className="app-scrollbar relative h-full overflow-y-auto px-4">
      {resultReady ? (
        <button type="button" className="sticky top-2 z-10 mx-auto mt-2 block rounded-full border border-edge-subtle bg-surface-overlay px-3 py-1.5 text-xs font-medium shadow-sm" onClick={() => { resultRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }); setResultReady(false); }}>
          Result ready · View result
        </button>
      ) : null}
      <div ref={resultRef}>
      <Section title="Result">
        {node.result !== null && node.result !== undefined ? <Scalar value={node.result} /> : (
          <p className="text-sm text-muted-foreground">
            {['pending', 'ready', 'queued'].includes(node.status)
              ? 'This node has not started yet.'
              : node.status === 'running'
                ? 'Waiting for this node to finish…'
                : 'No committed result.'}
          </p>
        )}
      </Section>
      </div>
      <Section title="Live output">
        {detail?.approval?.status === 'pending' ? (
          <div className="mb-3 rounded-md border border-state-warning/30 bg-state-warning/5 p-3">
            <div className="flex items-start gap-2">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-state-warning" />
              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium">{detail.approval.title}</p>
                <p className="mt-1 text-xs leading-5 text-muted-foreground">{detail.approval.prompt_text}</p>
                <div className="mt-3 flex gap-2">
                  <Button size="sm" disabled={approvalPending} onClick={() => onApproval('approve')}>Approve tool</Button>
                  <Button variant="outline" size="sm" disabled={approvalPending} onClick={() => onApproval('deny')}>Deny</Button>
                </div>
              </div>
            </div>
          </div>
        ) : null}
        {node.current_activity ? (
          <div className="mb-3 flex items-start gap-2 rounded-md bg-surface-sunken px-3 py-2 text-sm">
            <Clock3 className="mt-0.5 h-4 w-4 shrink-0 text-state-running" />
            <span>{node.current_activity}</span>
          </div>
        ) : null}
        {chunks.length ? (
          <div className="space-y-3">
            {chunks.map((chunk) => (
              <article key={chunk.seq} className="border-l-2 border-edge-subtle pl-3 text-sm leading-6" style={chunks.length > 50 ? { contentVisibility: 'auto', containIntrinsicSize: '0 96px' } : undefined}>
                <div className="mb-1 flex items-center justify-between text-xs text-content-tertiary">
                  <span>{chunk.kind.replaceAll('_', ' ')}</span>
                  <span className="tabular-nums">#{chunk.seq}</span>
                </div>
                <PublicTraceEntry payload={chunk.payload} />
              </article>
            ))}
          </div>
        ) : <p className="text-sm text-muted-foreground">No public output has been recorded.</p>}
      </Section>
      {Object.keys(node.error ?? {}).length ? (
        <Section title="Error">
          <div className="flex gap-2 rounded-md border border-state-danger/30 bg-state-danger/5 p-3 text-sm">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-state-danger" />
            <Scalar value={node.error} />
          </div>
        </Section>
      ) : null}
      {detail?.attempts?.length ? (
        <Section title="Attempts">
          {detail.attempts.map((attempt, index) => (
            <Field key={String(attempt.attempt ?? index)} label={`Attempt ${String(attempt.attempt ?? index + 1)}`}>
              <Scalar value={{ status: attempt.status, heartbeat: attempt.heartbeat_at, usage: attempt.usage }} />
            </Field>
          ))}
        </Section>
      ) : null}
      {newUpdates > 0 ? (
        <button type="button" className="sticky bottom-3 z-10 mx-auto mb-3 flex items-center gap-1 rounded-full border border-edge-subtle bg-surface-overlay px-3 py-1.5 text-xs font-medium shadow-sm" onClick={jumpToLatest}>
          <ChevronDown className="h-3.5 w-3.5" /> {newUpdates} new {newUpdates === 1 ? 'update' : 'updates'} · Jump to latest
        </button>
      ) : null}
      <span className="sr-only" role="status">{['succeeded', 'failed', 'cancelled'].includes(node.status) ? `Node ${node.status}` : ''}</span>
    </div>
  );
}

export function ExecutionNodeInspector({
  node,
  detail,
  tab,
  onTabChange,
  onClose,
  onCancel,
  onApproval,
  approvalPending,
}: {
  node: ExecutionNodeRun;
  detail?: ExecutionNodeDetail;
  tab: 'configuration' | 'output';
  onTabChange: (tab: 'configuration' | 'output') => void;
  onClose: () => void;
  onCancel: () => void;
  onApproval: (decision: 'approve' | 'deny') => void;
  approvalPending: boolean;
}) {
  const canCancel = node.node_type === 'subagent' && ['pending', 'ready', 'queued', 'running'].includes(node.status);
  const outputScrollMemory = useRef<Record<string, OutputScrollState>>({});
  const getOutputScrollState = useCallback(
    (nodeRunId: string) => outputScrollMemory.current[nodeRunId],
    [],
  );
  const setOutputScrollState = useCallback(
    (nodeRunId: string, state: OutputScrollState) => {
      outputScrollMemory.current[nodeRunId] = state;
    },
    [],
  );
  return (
    <aside className="flex h-full min-h-0 w-[min(34vw,520px)] min-w-[380px] flex-col border-l border-edge-subtle bg-surface-raised max-lg:absolute max-lg:inset-y-0 max-lg:right-0 max-lg:z-20 max-lg:w-[min(92vw,520px)] max-lg:min-w-0 max-lg:shadow-xl" aria-label={`${node.definition.title || node.node_path} inspector`}>
      <div className="flex min-h-14 shrink-0 items-center gap-3 border-b border-edge-subtle px-4">
        <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-surface-sunken"><Bot className="h-4 w-4" /></span>
        <div className="min-w-0 flex-1">
          <h3 className="truncate text-sm font-semibold">{node.definition.title || node.node_path}</h3>
          <div className="mt-0.5 flex items-center gap-2 text-xs text-muted-foreground">
            <StatusBadge status={tone(node.status)}>{node.status.replace('_', ' ')}</StatusBadge>
            {node.current_attempt > 0 ? <span>attempt {node.current_attempt}</span> : null}
          </div>
        </div>
        {canCancel ? (
          <Button variant="outline" size="sm" className="gap-1.5" onClick={onCancel}>
            <Square className="h-3.5 w-3.5" /> Cancel node
          </Button>
        ) : null}
        <Button variant="ghost" size="icon" className="toolbar-icon-button" onClick={onClose} aria-label="Close node inspector">
          <X className="h-4 w-4" />
        </Button>
      </div>
      <Tabs value={tab} onValueChange={(value) => onTabChange(value as 'configuration' | 'output')} className="flex min-h-0 flex-1 flex-col">
        <TabsList variant="underline" className="shrink-0 px-4">
          <TabsTrigger value="configuration">Configuration</TabsTrigger>
          <TabsTrigger value="output">
            Output {node.status === 'running' ? <span className="ml-1 h-1.5 w-1.5 rounded-full bg-state-running motion-safe:animate-pulse" /> : null}
          </TabsTrigger>
        </TabsList>
        <TabsContent value="configuration" className="app-scrollbar m-0 min-h-0 flex-1 overflow-y-auto">
          <Configuration node={node} />
        </TabsContent>
        <TabsContent value="output" className="m-0 min-h-0 flex-1 overflow-hidden">
          <Output
            key={node.node_run_id}
            node={node}
            detail={detail}
            getScrollState={getOutputScrollState}
            setScrollState={setOutputScrollState}
            onApproval={onApproval}
            approvalPending={approvalPending}
          />
        </TabsContent>
      </Tabs>
    </aside>
  );
}
