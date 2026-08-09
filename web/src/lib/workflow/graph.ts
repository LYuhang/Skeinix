/**
 * Pure graph traversal helpers over a workflow draft dict.
 *
 * A workflow on the wire is a flat dict keyed by `node_id` (plus reserved
 * `__*` keys). Directed edges are stored as each node's `children: string[]`
 * (parent → child). The graph is a DAG (loops use pairing pointers, not
 * `children`).
 *
 * `ancestorsOf` answers "which nodes run BEFORE `nodeId`?" — the predecessor
 * (upstream) set, used by the input-field Reference picker so a node can only
 * reference the OUTPUT of nodes that precede it (never its own descendants).
 * We build a reverse adjacency (child → parents) from the forward `children`
 * arrays, then BFS upstream from `nodeId`. The node itself is excluded.
 */

interface RawNode {
  children?: unknown;
  node_name?: unknown;
  output_fields?: unknown;
  node_type?: unknown;
  node_config?: unknown;
}

function isNodeKey(key: string): boolean {
  return !key.startsWith('__');
}

/**
 * The set of ancestor (predecessor) node ids reachable by walking edges
 * BACKWARDS from `nodeId`. Excludes `nodeId` itself and all descendants.
 *
 * Returns an empty set when the draft is null/undefined or `nodeId` is absent.
 * Resilient to cycles (a malformed non-DAG graph won't loop forever — `seen`
 * gates re-visits).
 */
export function ancestorsOf(
  draft: Record<string, unknown> | null | undefined,
  nodeId: string,
): Set<string> {
  const ancestors = new Set<string>();
  if (!draft) return ancestors;

  // Build reverse adjacency: child id → list of parent ids.
  const parents = new Map<string, string[]>();
  for (const [id, raw] of Object.entries(draft)) {
    if (!isNodeKey(id)) continue;
    const node = raw as RawNode | null;
    const children = Array.isArray(node?.children)
      ? (node!.children as unknown[])
      : [];
    for (const child of children) {
      if (typeof child !== 'string') continue;
      const list = parents.get(child);
      if (list) list.push(id);
      else parents.set(child, [id]);
    }
  }

  // BFS upstream from nodeId. The frontier never includes nodeId itself
  // (we only enqueue parents), so the self-node is naturally excluded.
  const queue: string[] = [...(parents.get(nodeId) ?? [])];
  while (queue.length > 0) {
    const current = queue.shift() as string;
    if (ancestors.has(current)) continue;
    ancestors.add(current);
    for (const parent of parents.get(current) ?? []) {
      if (!ancestors.has(parent)) queue.push(parent);
    }
  }

  return ancestors;
}

/**
 * For every node id, the set of `LoopBeginNode` ids whose body STRICTLY contains
 * it. The loop body of a begin node `B` (with `node_config.loop_end_node_id` = `E`)
 * is every node reachable from `B`'s `children` by following `children` edges,
 * stopping at (and EXCLUDING) `E`. The boundaries `B` and `E` are NOT members of
 * the loop they delimit — only the interior nodes are.
 *
 * Loops nest properly, so an inner-body node carries BOTH the inner and the outer
 * begin ids in its membership set. We BFS each loop independently (own `visited`
 * set) so cycles in a malformed non-DAG graph can't loop forever, and we never
 * enqueue/cross `E` (it bounds the body).
 *
 * A loop whose `loop_end_node_id` is null/absent or points to a node not present
 * in the draft is SKIPPED entirely — we can't bound its body, so we degrade to no
 * scoping for it (the safe, less-restrictive choice). Malformed nodes (non-object,
 * missing `children`) are tolerated exactly as `ancestorsOf` tolerates them.
 */
export function getNodeLoopMemberships(
  draft: Record<string, unknown> | null | undefined,
): Map<string, Set<string>> {
  const memberships = new Map<string, Set<string>>();
  if (!draft) return memberships;

  for (const [beginId, raw] of Object.entries(draft)) {
    if (!isNodeKey(beginId)) continue;
    if (!raw || typeof raw !== 'object') continue;
    const node = raw as RawNode;
    if (node.node_type !== 'LoopBeginNode') continue;

    const config =
      node.node_config && typeof node.node_config === 'object'
        ? (node.node_config as Record<string, unknown>)
        : {};
    const endId = config.loop_end_node_id;
    // Can't bound the body → skip (degrade to no scoping for this loop).
    if (typeof endId !== 'string' || !(endId in draft)) continue;

    // BFS the body from B's children, following `children`, never crossing E.
    const visited = new Set<string>();
    const beginChildren = Array.isArray(node.children)
      ? (node.children as unknown[])
      : [];
    const queue: string[] = [];
    for (const child of beginChildren) {
      if (typeof child === 'string' && child !== endId) queue.push(child);
    }
    while (queue.length > 0) {
      const current = queue.shift() as string;
      if (current === endId || visited.has(current)) continue;
      visited.add(current);
      const currentRaw = draft[current] as RawNode | null | undefined;
      const currentChildren = Array.isArray(currentRaw?.children)
        ? (currentRaw!.children as unknown[])
        : [];
      for (const child of currentChildren) {
        if (typeof child !== 'string') continue;
        if (child === endId || visited.has(child)) continue;
        queue.push(child);
      }
    }

    // Tag every interior node with this begin id.
    for (const memberId of visited) {
      const set = memberships.get(memberId);
      if (set) set.add(beginId);
      else memberships.set(memberId, new Set([beginId]));
    }
  }

  return memberships;
}

/**
 * Reference dropdown candidates for `selfId`: every `producer_node_name.output_field`
 * whose producer is an ANCESTOR (predecessor) of `selfId` in the DAG. A field can
 * only reference the output of a node that runs BEFORE it, so we restrict the list
 * to the upstream set (`ancestorsOf`), which naturally excludes the node itself AND
 * all of its descendants. Computed from the draft (the source of truth) so the list
 * reflects in-flight edits.
 *
 * LOOP-SCOPE FILTERING: variable scope is isolated per loop — a node OUTSIDE a loop
 * cannot reference a node INSIDE that loop directly (it reaches loop internals only
 * through the begin node's aggregated `loop_output` / `i`). We enforce this with a
 * SUBSET rule: an ancestor `A` is offered ONLY IF the set of loops `A` lives inside
 * is a subset of the set of loops `selfId` lives inside; otherwise `A` is out of
 * scope and skipped. Because a loop's `LoopBeginNode` is a boundary (not a member
 * of its own loop), it is offered as an ordinary ancestor, so its `loop_output`/`i`
 * fields surface naturally. This composes for nested loops: an inner-body node
 * carries {outer, inner} and is hidden from an outer-only self ({outer,inner} ⊄
 * {outer}), while the inner begin carries {outer} ⊆ {outer} and IS offered.
 *
 * Shared by the input-field pickers (`NodeTab`) and the LoopBegin init/end value
 * pickers (`LoopBeginNodeEditor`) — both must offer ONLY predecessor outputs.
 */
export function referenceCandidatesFromAncestors(
  draft: Record<string, unknown> | null | undefined,
  selfId: string,
): string[] {
  if (!draft) return [];
  const ancestors = ancestorsOf(draft, selfId);
  const memberships = getNodeLoopMemberships(draft);
  const selfLoops = memberships.get(selfId) ?? new Set<string>();
  const out: string[] = [];
  for (const [id, raw] of Object.entries(draft)) {
    if (!ancestors.has(id)) continue;
    if (!raw || typeof raw !== 'object') continue;
    // Loop-scope subset rule: skip ancestors living in a loop selfId isn't in.
    const aLoops = memberships.get(id) ?? new Set<string>();
    let inScope = true;
    for (const loopId of aLoops) {
      if (!selfLoops.has(loopId)) {
        inScope = false;
        break;
      }
    }
    if (!inScope) continue;
    const node = raw as RawNode;
    const producer =
      typeof node.node_name === 'string' && node.node_name ? node.node_name : id;
    const outputs =
      node.output_fields && typeof node.output_fields === 'object'
        ? (node.output_fields as Record<string, unknown>)
        : {};
    for (const field of Object.keys(outputs)) {
      out.push(`${producer}.${field}`);
    }
  }
  return out;
}
