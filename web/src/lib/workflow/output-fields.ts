/**
 * Output-field candidates for the batch-run "output columns" config.
 *
 * The batch output table is built from an explicit, ordered list of columns.
 * A "field" column pulls a value from some node's `output_fields`. This helper
 * lists EVERY node's output fields across the current workflow draft so the
 * column-source dropdown can offer them — unlike `referenceCandidatesFromAncestors`
 * (which is ancestor-scoped for the input Reference picker), the batch output
 * may surface any node's output, so this lists ALL nodes.
 *
 * A workflow draft is a flat dict keyed by `node_id` plus the reserved
 * `__meta__` (and other `__*`) keys, which we skip. Each node carries
 * `node_name` (user-facing, referenced by other nodes) and `output_fields`
 * (`{fieldName: {...}}`). We emit `{ node, field, label }` where `label` is the
 * `node_name.field` shown in the dropdown; `node`/`field` are stored separately
 * on the column (the backend takes them as separate keys).
 */

interface RawNode {
  node_name?: unknown;
  output_fields?: unknown;
}

function isNodeKey(key: string): boolean {
  return !key.startsWith('__');
}

export interface OutputFieldCandidate {
  /** Producer node's `node_name` (falls back to the node id if unnamed). */
  node: string;
  /** The output field name. */
  field: string;
  /** Display label `node.field` — also the dropdown option value. */
  label: string;
}

/**
 * List every node's output fields in the draft. Skips `__meta__`/`__*`,
 * tolerates nodes that are non-objects or declare no `output_fields`.
 * Order follows `Object.keys` insertion order (node, then field).
 */
export function outputFieldCandidates(
  draft: Record<string, unknown> | null | undefined,
): OutputFieldCandidate[] {
  if (!draft) return [];
  const out: OutputFieldCandidate[] = [];
  for (const [id, raw] of Object.entries(draft)) {
    if (!isNodeKey(id)) continue;
    if (!raw || typeof raw !== 'object') continue;
    const node = raw as RawNode;
    const producer =
      typeof node.node_name === 'string' && node.node_name ? node.node_name : id;
    const outputs =
      node.output_fields && typeof node.output_fields === 'object'
        ? (node.output_fields as Record<string, unknown>)
        : {};
    for (const field of Object.keys(outputs)) {
      out.push({ node: producer, field, label: `${producer}.${field}` });
    }
  }
  return out;
}
