import { memo, useMemo } from 'react';
import dagre from 'dagre';
import {
  Background,
  Controls,
  Handle,
  Position,
  ReactFlow,
  ReactFlowProvider,
  type Edge,
  type Node,
  type NodeProps,
  type NodeTypes,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import {
  Bot,
  CircleStop,
  Play,
} from 'lucide-react';

import { StatusBadge, type SemanticStatus } from '@/components/ui/status';
import type {
  ExecutionNodeRun,
  ExecutionPlanDefinitionNode,
} from '@/lib/api/execution-plans';
import { cn } from '@/lib/utils';

interface PlanGraphNodeData extends Record<string, unknown> {
  definition: ExecutionPlanDefinitionNode;
  run?: ExecutionNodeRun;
  label: string;
  incomingCount: number;
}

const ICONS = { start: Play, subagent: Bot, end: CircleStop };
const TYPE_TONE = {
  start: 'border-state-success/30 bg-state-success/5',
  subagent: 'border-focus/25 bg-focus/[0.035]',
  end: 'border-content-tertiary/35 bg-surface-sunken/70',
};
const ICON_TONE = {
  start: 'bg-state-success/10 text-state-success',
  subagent: 'bg-focus/10 text-focus',
  end: 'bg-content-tertiary/10 text-content-secondary',
};

function statusTone(status?: string): SemanticStatus {
  if (status === 'succeeded') return 'success';
  if (status === 'failed') return 'danger';
  if (status === 'cancelled' || status === 'skipped') return 'neutral';
  if (status === 'cancel_requested') return 'warning';
  if (status === 'running') return 'running';
  return 'neutral';
}

const PlanNode = memo(function PlanNode({ data, selected }: NodeProps<Node<PlanGraphNodeData>>) {
  const definition = data.definition;
  const status = data.run?.status ?? 'pending';
  const Icon = ICONS[definition.type];
  const summary = definition.type === 'subagent'
    ? definition.task
    : definition.type === 'start' && (definition.next?.length ?? 0) > 1
      ? `Splits into ${definition.next?.length ?? 0} parallel branches`
      : definition.type === 'end' && data.incomingCount > 1
        ? `Merges ${data.incomingCount} parallel branches`
        : definition.type;
  return (
    <button
      type="button"
      className={cn(
        'group relative w-[224px] rounded-lg border text-left shadow-sm transition-[border-color,box-shadow] duration-feedback focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/40',
        TYPE_TONE[definition.type],
        selected && 'border-focus shadow-md ring-1 ring-focus/20',
        status === 'running' && 'border-l-[3px] border-l-state-running motion-safe:animate-node-breathe',
        status === 'cancelled' && 'border-2 border-state-danger',
        status === 'failed' && 'border-state-danger',
      )}
      aria-label={`${data.label}, ${status}`}
      data-status={status}
      data-node-type={definition.type}
    >
      <Handle type="target" position={Position.Top} className="!h-2.5 !w-2.5 !border-2 !border-background !bg-content-tertiary" />
      <span className="flex items-center gap-2 border-b border-edge-subtle px-3 py-2">
        <span className={cn('flex h-6 w-6 shrink-0 items-center justify-center rounded-md', ICON_TONE[definition.type])}>
          <Icon className="h-3.5 w-3.5" />
        </span>
        <span className="min-w-0 flex-1 truncate text-[13px] font-medium">{data.label}</span>
        <StatusBadge status={statusTone(status)}>{status.replace('_', ' ')}</StatusBadge>
      </span>
      <span className="block min-h-10 px-3 py-2 text-xs leading-5 text-muted-foreground">
        <span className="line-clamp-2">{summary || 'No additional configuration'}</span>
      </span>
      <Handle type="source" position={Position.Bottom} className="!h-2.5 !w-2.5 !border-2 !border-background !bg-content-tertiary" />
    </button>
  );
});

const NODE_TYPES: NodeTypes = { executionPlan: PlanNode as NodeTypes[string] };

function topology(definitions: ExecutionPlanDefinitionNode[]) {
  const incoming = new Map(definitions.map((definition) => [definition.id, 0]));
  for (const definition of definitions) {
    for (const target of definition.next ?? []) {
      incoming.set(target, (incoming.get(target) ?? 0) + 1);
    }
  }
  const rawNodes: Node<PlanGraphNodeData>[] = definitions.map((definition) => ({
    id: definition.id,
    type: 'executionPlan',
    position: { x: 0, y: 0 },
    data: {
      definition,
      label: definition.title || definition.id,
      incomingCount: incoming.get(definition.id) ?? 0,
    },
  }));
  const edges: Edge[] = [];
  for (const definition of definitions) {
    for (const target of definition.next ?? []) {
      edges.push({
        id: `${definition.id}->${target}`,
        source: definition.id,
        target,
        className: (definition.next?.length ?? 0) > 1
          ? '[&_.react-flow__edge-path]:stroke-focus/55'
          : undefined,
      });
    }
  }
  const graph = new dagre.graphlib.Graph();
  graph.setDefaultEdgeLabel(() => ({}));
  graph.setGraph({ rankdir: 'TB', nodesep: 48, ranksep: 76, marginx: 32, marginy: 32 });
  rawNodes.forEach((node) => graph.setNode(node.id, { width: 224, height: 92 }));
  edges.forEach((edge) => graph.setEdge(edge.source, edge.target));
  dagre.layout(graph);
  return {
    edges,
    nodes: rawNodes.map((node) => {
      const point = graph.node(node.id);
      return { ...node, position: { x: point.x - 112, y: point.y - 46 } };
    }),
  };
}

function ExecutionPlanGraphInner({
  definitions,
  runs,
  selectedNodePath,
  onSelect,
}: {
  definitions: ExecutionPlanDefinitionNode[];
  runs: ExecutionNodeRun[];
  selectedNodePath: string | null;
  onSelect: (nodePath: string) => void;
}) {
  // Layout is based only on immutable topology. Status/output polling never
  // moves nodes or resets the user's viewport.
  const base = useMemo(() => topology(definitions), [definitions]);
  const runByPath = useMemo(() => new Map(runs.map((run) => [run.node_path, run])), [runs]);
  const nodes = useMemo(() => base.nodes.map((node) => ({
    ...node,
    selected: node.id === selectedNodePath,
    data: { ...node.data, run: runByPath.get(node.id) },
  })), [base.nodes, runByPath, selectedNodePath]);
  const edges = useMemo(() => base.edges.map((edge) => ({
    ...edge,
    animated: runByPath.get(edge.target)?.status === 'running',
    className: 'motion-reduce:[&_.react-flow__edge-path]:transition-none',
  })), [base.edges, runByPath]);
  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      nodeTypes={NODE_TYPES}
      fitView
      minZoom={0.35}
      maxZoom={1.5}
      nodesDraggable={false}
      nodesConnectable={false}
      elementsSelectable
      onNodeClick={(_, node) => onSelect(node.id)}
      proOptions={{ hideAttribution: true }}
    >
      <Background gap={24} size={1} />
      <Controls showInteractive={false} position="bottom-left" />
    </ReactFlow>
  );
}

export function ExecutionPlanGraph(props: Parameters<typeof ExecutionPlanGraphInner>[0]) {
  return <ReactFlowProvider><ExecutionPlanGraphInner {...props} /></ReactFlowProvider>;
}
