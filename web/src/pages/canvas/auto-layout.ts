/**
 * Dagre-powered auto-layout helper.
 *
 * Pure function — given a list of xyflow `Node`/`Edge` pairs, compute a
 * left-to-right layered layout and return a fresh `nodes` array with new
 * `position` values. Edges are untouched (xyflow re-routes them based on
 * source/target positions). `rankdir: 'LR'` (horizontal) matches the
 * node-graph / LLM-workflow convention (n8n / LangFlow / Coze / Dify) and
 * reads better for the branch/parallel fan-out this canvas supports; the
 * `CustomNode` handles are correspondingly Left (target) / Right (source).
 *
 * Node dimensions are hardcoded to (220, 100) — the same size the
 * `CustomNode` renders at. Dagre returns the *center* of each node, so
 * we subtract half the dimensions to convert back into xyflow's
 * top-left-origin coordinate system.
 *
 * T7 ships this helper without a UI consumer; a future task will wire a
 * "Layout" toolbar button (and/or a context-menu action) to call it.
 */
import dagre from 'dagre';
import type { Edge, Node } from '@xyflow/react';
import { workflowDictToNodesEdges } from '@/pages/canvas/Canvas';

const NODE_WIDTH = 220;
const NODE_HEIGHT = 100;

export function autoLayout(nodes: Node[], edges: Edge[]): Node[] {
  const g = new dagre.graphlib.Graph();
  g.setGraph({ rankdir: 'LR', nodesep: 60, ranksep: 80 });
  g.setDefaultEdgeLabel(() => ({}));
  for (const n of nodes) g.setNode(n.id, { width: NODE_WIDTH, height: NODE_HEIGHT });
  for (const e of edges) g.setEdge(e.source, e.target);
  dagre.layout(g);
  return nodes.map((n) => {
    const { x, y } = g.node(n.id);
    return { ...n, position: { x: x - NODE_WIDTH / 2, y: y - NODE_HEIGHT / 2 } };
  });
}

/**
 * Apply dagre auto-layout to a flat workflow dict IN PLACE, writing the
 * computed `{x, y}` back into each node's `__attributes__`. Used by:
 *   - the "Tidy up" toolbar action (re-arrange every node), and
 *   - the JSON-upload mutator (`onlyPositionless: true` — lay out just the
 *     freshly-loaded nodes that have no position, so existing layout is kept).
 *
 * Operates on the SAME dict the caller passes (the `applyEdit` clone), so the
 * mutation is captured in one undo step. Reserved `__…__` keys are ignored by
 * `workflowDictToNodesEdges`. Returns the dict for chaining.
 */
export function layoutWorkflowDict(
  wf: Record<string, unknown>,
  opts: { onlyPositionless?: boolean } = {},
): Record<string, unknown> {
  const { nodes, edges } = workflowDictToNodesEdges(wf);
  const laid = autoLayout(nodes, edges);
  for (const n of laid) {
    const node = wf[n.id] as Record<string, unknown> | undefined;
    if (!node || typeof node !== 'object') continue;
    const attrs = (node.__attributes__ as Record<string, unknown> | undefined) ?? {};
    if (opts.onlyPositionless && hasPosition(attrs)) continue;
    node.__attributes__ = { ...attrs, x: n.position.x, y: n.position.y };
  }
  return wf;
}

function hasPosition(attrs: Record<string, unknown>): boolean {
  return typeof attrs.x === 'number' && typeof attrs.y === 'number';
}
