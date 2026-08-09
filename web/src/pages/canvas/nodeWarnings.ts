/**
 * `nodeWarnings(draft)` — a PURE selector that surfaces the editor's cheap,
 * by-construction knowledge of node validity AT the canvas (per-node ⚠ badge),
 * NOT buried in the inspector and NOT waiting for the server Check.
 *
 * Contract
 * --------
 * Input: the live workflow draft (the flat dict keyed by `node_id`, with a
 *   reserved `__meta__` and other `__`-prefixed keys ignored), or `null`.
 * Output: `Map<nodeId, string[]>` — a node id maps to a list of human-readable
 *   warning message KEYS only for nodes that have ≥1 warning. A node with no
 *   warnings is ABSENT from the map (so `map.has(id)` / `map.get(id)` both work
 *   as a presence test). The strings are i18n keys (`canvas.warn.*`) the badge
 *   tooltip resolves via `t()`.
 *
 * This is deliberately NOT a validator clone. It mirrors ONLY the cheap LOCAL
 * checks the editor already knows by construction — the same rules the route
 * Check now enforces, surfaced early so the user sees a problem before clicking
 * Check. The expensive global checks (reachability, DAG-ness, exactly-one Start)
 * stay on the server.
 *
 * Checks (mirror engine `condition.py` / `parallel.py` / `loop.py` / the
 * reference rule in `workflow.py`):
 *   1. ConditionNode — any non-"others" row with an empty `condition_str`
 *      (`canvas.warn.conditionEmptyExpr`); OR the set of non-empty
 *      `next_node_id`s ≠ the `children` set (`canvas.warn.conditionMismatch`).
 *      The mandatory "others" fallback being absent is reported as
 *      `canvas.warn.conditionNoOthers`.
 *   2. ParallelStartNode / LoopBeginNode with a null/unset pairing pointer
 *      (`parallel_end_node_id` / `loop_end_node_id`) →
 *      `canvas.warn.unpairedParallel` / `canvas.warn.unpairedLoop`.
 *      Symmetrically ParallelEndNode / LoopEndNode with an unset back-pointer.
 *   3. Any node whose `input_fields[*].reference` names a `node_name` that does
 *      not exist (`canvas.warn.danglingRefNode`) or an `output_field` that
 *      node does not declare (`canvas.warn.danglingRefField`).
 */

type Dict = Record<string, unknown>;

function isNodeRecord(v: unknown): v is Dict {
  return !!v && typeof v === 'object' && !Array.isArray(v);
}

function getConfig(node: Dict): Dict {
  const cfg = node.node_config;
  return isNodeRecord(cfg) ? cfg : {};
}

/** Build a `node_name -> Set<output_field>` index for reference checks. */
function buildOutputIndex(wf: Dict): Map<string, Set<string>> {
  const index = new Map<string, Set<string>>();
  for (const [key, value] of Object.entries(wf)) {
    if (key.startsWith('__')) continue;
    if (!isNodeRecord(value)) continue;
    const name = value.node_name;
    if (typeof name !== 'string' || !name) continue;
    const outputs = isNodeRecord(value.output_fields)
      ? new Set(Object.keys(value.output_fields))
      : new Set<string>();
    index.set(name, outputs);
  }
  return index;
}

/**
 * Parse the first two segments of a reference string
 * (`node_name.output_field[idx].sub` → `{ node, field }`). Mirrors the engine
 * `workflow.py` partition logic so the local check and the server agree.
 */
function parseReference(ref: string): { node: string; field: string | null } {
  const dot = ref.indexOf('.');
  if (dot < 0) return { node: ref, field: null };
  const head = ref.slice(0, dot);
  const rest = ref.slice(dot + 1);
  if (!rest) return { node: head, field: null };
  // second segment ends at the next "." or "["
  const first = rest.replace('[', '.').split('.', 1)[0];
  return { node: head, field: first || null };
}

function checkCondition(node: Dict, warnings: string[]): void {
  const cfg = getConfig(node);
  const conditions = Array.isArray(cfg.conditions)
    ? (cfg.conditions as Dict[])
    : [];
  const isOthers = (c: Dict) =>
    (typeof c.condition_str === 'string' && c.condition_str.trim() === 'others') ||
    c.condition_name === 'others';

  // 1a. non-"others" row with an empty condition_str.
  const hasEmptyExpr = conditions.some(
    (c) =>
      !isOthers(c) &&
      (typeof c.condition_str !== 'string' || c.condition_str.trim() === ''),
  );
  if (hasEmptyExpr) warnings.push('canvas.warn.conditionEmptyExpr');

  // 1b. mandatory "others" fallback.
  if (!conditions.some(isOthers)) warnings.push('canvas.warn.conditionNoOthers');

  // 1c. conditions (non-empty next_node_id) must equal children.
  const children = Array.isArray(node.children)
    ? (node.children as unknown[]).filter((c): c is string => typeof c === 'string')
    : [];
  const condTargets = conditions
    .map((c) => c.next_node_id)
    .filter((id): id is string => typeof id === 'string' && id !== '');
  const condSet = new Set(condTargets);
  const childSet = new Set(children);
  const sameSize = condSet.size === childSet.size;
  const allShared = [...condSet].every((id) => childSet.has(id));
  if (!sameSize || !allShared) warnings.push('canvas.warn.conditionMismatch');
}

function checkPairing(
  node: Dict,
  nodeType: string,
  warnings: string[],
): void {
  const cfg = getConfig(node);
  const isUnset = (ptr: unknown) =>
    ptr === null || ptr === undefined || ptr === '';
  if (nodeType === 'ParallelStartNode' && isUnset(cfg.parallel_end_node_id)) {
    warnings.push('canvas.warn.unpairedParallel');
  }
  if (nodeType === 'ParallelEndNode' && isUnset(cfg.parallel_start_node_id)) {
    warnings.push('canvas.warn.unpairedParallel');
  }
  if (nodeType === 'LoopBeginNode' && isUnset(cfg.loop_end_node_id)) {
    warnings.push('canvas.warn.unpairedLoop');
  }
  if (nodeType === 'LoopEndNode' && isUnset(cfg.loop_begin_node_id)) {
    warnings.push('canvas.warn.unpairedLoop');
  }
}

function checkReferences(
  node: Dict,
  outputIndex: Map<string, Set<string>>,
  warnings: string[],
): void {
  const inputs = node.input_fields;
  if (!isNodeRecord(inputs)) return;
  let danglingNode = false;
  let danglingField = false;
  for (const field of Object.values(inputs)) {
    if (!isNodeRecord(field)) continue;
    const ref = field.reference;
    if (typeof ref !== 'string' || !ref) continue;
    const { node: refNode, field: refField } = parseReference(ref);
    const outputs = outputIndex.get(refNode);
    if (!outputs) {
      danglingNode = true;
      continue;
    }
    if (refField !== null && !outputs.has(refField)) {
      danglingField = true;
    }
  }
  if (danglingNode) warnings.push('canvas.warn.danglingRefNode');
  if (danglingField) warnings.push('canvas.warn.danglingRefField');
}

export function nodeWarnings(draft: Dict | null): Map<string, string[]> {
  const result = new Map<string, string[]>();
  if (!draft) return result;

  const outputIndex = buildOutputIndex(draft);

  for (const [key, value] of Object.entries(draft)) {
    if (key.startsWith('__')) continue;
    if (!isNodeRecord(value)) continue;
    const warnings: string[] = [];
    const nodeType = typeof value.node_type === 'string' ? value.node_type : '';

    if (nodeType === 'ConditionNode') checkCondition(value, warnings);
    checkPairing(value, nodeType, warnings);
    checkReferences(value, outputIndex, warnings);

    if (warnings.length > 0) result.set(key, warnings);
  }

  return result;
}
