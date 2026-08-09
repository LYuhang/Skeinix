/**
 * Per-node-type flag: does this node's OUTPUT mirror its INPUT?
 *
 * For some node types the declared output_fields are not independently
 * editable — they ARE a projection of the input_fields. The canonical case
 * is **StartNode**: its outputs are exactly the declared workflow inputs
 * (name + type), so the engine resolves `start_node_name.field` references
 * against the same names the user typed as inputs.
 *
 * When `outputsFollowInputs(nodeType)` is true the inspector renders the
 * output section READ-ONLY as a VIEW of the inputs (mirror name + type,
 * dropping value/reference) and hides the add/× controls — the user never
 * hand-edits a mirrored output. The mirror is materialized into the draft's
 * `output_fields` in the SAME `applyEdit` that edits the inputs (see
 * `NodeTab.onInputFieldsChange` + `materializeMirroredOutputs`), so the
 * persisted workflow always carries a faithful output_fields without the
 * editor double-writing on unrelated edits.
 *
 * Add a node_type here when (and only when) its engine semantics make the
 * outputs a pure function of the inputs.
 */

const OUTPUTS_FOLLOW_INPUTS: ReadonlySet<string> = new Set<string>([
  'StartNode',
  // EndNode.check (engine end.py) requires output_fields to mirror input_fields
  // EXACTLY (same names + types) — auto-mirror them read-only or the user hits a
  // route-Check failure they can't fix in the UI.
  'EndNode',
]);

export function outputsFollowInputs(nodeType: string | undefined): boolean {
  return nodeType !== undefined && OUTPUTS_FOLLOW_INPUTS.has(nodeType);
}

type InputFieldEntry = { type?: string; value?: unknown; reference?: string };
type OutputFieldEntry = { type: string; description?: string };

/**
 * Project `input_fields` → mirrored `output_fields`: keep name + type, drop
 * value/reference (outputs carry no literal). `type` is always set (defaults
 * to `'string'`). Insertion order is preserved.
 *
 * Used both to RENDER the read-only output mirror and to MATERIALIZE it into
 * the draft in the same `applyEdit` that edits a mirroring node's inputs
 * (also reusable at Save serialization by Stream 6).
 */
export function mirrorOutputsFromInputs(
  inputFields: Record<string, InputFieldEntry>,
): Record<string, OutputFieldEntry> {
  const out: Record<string, OutputFieldEntry> = {};
  for (const [name, entry] of Object.entries(inputFields ?? {})) {
    out[name] = { type: entry?.type ?? 'string' };
  }
  return out;
}
