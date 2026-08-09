/** Human-facing node reference: `node_name(node_id)`. Falls back to just the
 * id when the name is missing or equal to the id. `draft` is the workflow dict
 * (keyed by node_id; each value may have `node_name`). */
type Dict = Record<string, unknown>;
const isObj = (x: unknown): x is Dict => typeof x === 'object' && x !== null && !Array.isArray(x);

export function nodeNameOf(draft: unknown, nodeId: string): string | null {
  if (!isObj(draft)) return null;
  const n = draft[nodeId];
  if (isObj(n) && typeof n.node_name === 'string' && n.node_name) return n.node_name;
  return null;
}

export function nodeLabel(draft: unknown, nodeId: string): string {
  const name = nodeNameOf(draft, nodeId);
  return name && name !== nodeId ? `${name}(${nodeId})` : nodeId;
}

/** Replace bare `node_<n>` id tokens in a free-text string (e.g. a Check error
 * message) with `node_name(node_id)`. Only ids present in the draft are
 * rewritten; unknown tokens are left as-is. */
export function humanizeNodeRefs(text: string, draft: unknown): string {
  if (!text) return text;
  return text.replace(/node_\d+/g, (id) => {
    const name = nodeNameOf(draft, id);
    return name && name !== id ? `${name}(${id})` : id;
  });
}
