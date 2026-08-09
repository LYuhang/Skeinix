/**
 * Helpers for poking at a workflow dict without rebuilding the engine's
 * full node model in the browser.
 *
 * Background: a workflow on the wire is a flat dict keyed by `node_id`,
 * plus a reserved `__meta__` key (see legacy `BaseNode.GENERAL_NODE_SCHEMA`).
 * Each entry carries `node_type`, `input_fields`, etc. The Run-Batch modal
 * (T16) needs the list of `StartNode.input_fields` so it can let the user
 * map CSV columns onto workflow input names. Keeping the lookup in `lib/`
 * means tests and other surfaces (future scheduled-run modal, webhook UI)
 * can reuse the same shape instead of each rebuilding the traversal.
 *
 * Returned `type` is best-effort: legacy field entries record a primitive
 * type string (`"string" | "integer" | ...`); we surface it raw so the
 * caller can decide whether to render a hint. Defaults to `"string"` when
 * the field omits a type (matches legacy `FieldsEditor` behaviour).
 */

export interface StartNodeField {
  name: string;
  type: string;
}

interface FieldEntry {
  type?: string;
}

interface NodeEntry {
  node_type?: string;
  input_fields?: Record<string, FieldEntry>;
}

/**
 * Extract the StartNode's input-field signature from a workflow dict.
 *
 * Returns `[]` when:
 *   - the workflow is null/undefined,
 *   - no `StartNode` is present (malformed workflow — `Workflow.check`
 *     would reject server-side, but the UI must not crash),
 *   - the StartNode declares no `input_fields`.
 *
 * The order follows `Object.keys` insertion order, which matches what
 * the canvas inspector displays.
 */
export function getStartNodeFields(
  wf: Record<string, unknown> | null | undefined,
): StartNodeField[] {
  if (!wf) return [];
  for (const [key, raw] of Object.entries(wf)) {
    if (key.startsWith('__')) continue;
    const entry = raw as NodeEntry | null;
    if (!entry || entry.node_type !== 'StartNode') continue;
    const fields = entry.input_fields ?? {};
    return Object.entries(fields).map(([name, f]) => ({
      name,
      type: f?.type ?? 'string',
    }));
  }
  return [];
}

/**
 * True iff the workflow dict carries at least one `StartNode`. Skips the
 * reserved `__meta__`/`__*` keys. A null/undefined workflow has none.
 */
export function hasStartNode(
  wf: Record<string, unknown> | null | undefined,
): boolean {
  if (!wf) return false;
  for (const [key, raw] of Object.entries(wf)) {
    if (key.startsWith('__')) continue;
    const entry = raw as NodeEntry | null;
    if (entry && entry.node_type === 'StartNode') return true;
  }
  return false;
}

/**
 * The skeleton payload for a freshly-seeded StartNode — the SAME shape the
 * "Add node" dialog supplies for any node type (the store's `addNode` then
 * allocates `node_id`, defaults `node_name` to the id, and stamps the drop
 * position into `__attributes__`). Kept here so the onboarding seed (M1) and
 * a future manual "Add StartNode" share one definition.
 */
export const START_NODE_PAYLOAD: Record<string, unknown> = {
  node_type: 'StartNode',
  node_description: '',
  input_fields: {},
  output_fields: {},
  node_config: {},
  children: [],
};

/**
 * Pure guard for the onboarding seed (Stream 8 M1). Returns true iff the
 * CanvasPage mount effect should seed a StartNode for this draft:
 *   - NOT in read-only (pinned-version) mode,
 *   - the draft has been seeded from the server (`draft != null`),
 *   - and there is genuinely no StartNode yet.
 * (The "once per route" gate is the effect's own ref — that's stateful, not
 * a property of the draft, so it stays at the call site.)
 */
export function shouldSeedStartNode(
  draft: Record<string, unknown> | null | undefined,
  readOnly: boolean,
): boolean {
  if (readOnly) return false;
  if (draft == null) return false;
  return !hasStartNode(draft);
}
