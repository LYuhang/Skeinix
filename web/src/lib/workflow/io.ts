/**
 * Workflow JSON download / upload helpers (Stream 6).
 *
 * Pure functions only — no React, no store, no DOM. The toolbar's ⋯-More
 * menu calls these to (a) serialize the current draft for a `.json` file
 * download, and (b) parse a user-uploaded `.json` back into a node dict the
 * canvas can load.
 *
 * Upload portability rule:
 *   We KEEP `node_*` entries AND the complete `__meta__` object. Workflow
 *   settings such as `settings.code_requirements`, timeouts and network policy
 *   are part of the executable workflow contract and must survive a download →
 *   upload round trip. Other reserved / identity-shaped top-level keys remain
 *   excluded. The caller applies nodes + meta through one undoable edit.
 *
 * Validation:
 *   `parseUploadedWorkflow` throws a `WorkflowParseError` on
 *     - non-object JSON,
 *     - a dict with ZERO valid node entries,
 *     - any surviving node-keyed entry that is not a node-shaped object
 *       (a `node_type` string is the minimal shape check).
 *   Malformed JSON text throws before reaching here (JSON.parse) — the caller
 *   wraps the whole parse in try/catch and toasts.
 */

const NODE_KEY_RE = /^node_\d+$/;

export class WorkflowParseError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'WorkflowParseError';
  }
}

export type WorkflowDict = Record<string, unknown>;

function isPlainObject(x: unknown): x is Record<string, unknown> {
  return typeof x === 'object' && x !== null && !Array.isArray(x);
}

/**
 * Drop reserved / identity top-level keys; KEEP nodes and the complete
 * `__meta__` object.
 * Returns a fresh object (does not mutate the input).
 */
export function selectPortableWorkflow(wf: Record<string, unknown>): WorkflowDict {
  const out: WorkflowDict = {};
  for (const [key, value] of Object.entries(wf)) {
    if (NODE_KEY_RE.test(key) || key === '__meta__') out[key] = value;
  }
  return out;
}

/**
 * Serialize a draft to a pretty-printed JSON string for download. Includes
 * the full draft (nodes + `__attributes__` positions + `__meta__`) so a
 * round-trip download → re-upload preserves layout and execution settings.
 */
export function serializeWorkflow(draft: Record<string, unknown> | null): string {
  return JSON.stringify(draft ?? {}, null, 2);
}

/**
 * Parse uploaded JSON text (already `JSON.parse`d into a value) into the
 * portable workflow content. Preserves `__meta__` and validates its shape.
 *
 * @returns `{ workflow }` — nodes plus an optional `__meta__` object.
 * @throws  {WorkflowParseError} on a non-object, an empty node set, or a
 *          malformed node entry.
 */
export function parseUploadedWorkflow(parsed: unknown): { workflow: WorkflowDict } {
  if (!isPlainObject(parsed)) {
    throw new WorkflowParseError('Uploaded file is not a workflow object.');
  }
  const workflow = selectPortableWorkflow(parsed);
  const keys = Object.keys(workflow).filter((key) => NODE_KEY_RE.test(key));
  if (keys.length === 0) {
    throw new WorkflowParseError('No workflow nodes found in the uploaded file.');
  }
  for (const key of keys) {
    const entry = workflow[key];
    if (!isPlainObject(entry) || typeof entry.node_type !== 'string') {
      throw new WorkflowParseError(`Entry "${key}" is not a valid node.`);
    }
  }
  if ('__meta__' in workflow && !isPlainObject(workflow.__meta__)) {
    throw new WorkflowParseError('Entry "__meta__" is not a valid object.');
  }
  return { workflow };
}

/**
 * Build a download filename from the workflow name + version. Sanitizes the
 * name to a filesystem-safe slug and always ends in `.json`.
 */
export function downloadFilename(name?: string | null, version?: string | null): string {
  const base = (name && name.trim()) || 'workflow';
  const slug = base
    .trim()
    .replace(/[^\w\-.]+/g, '_')
    .replace(/^_+|_+$/g, '')
    .slice(0, 80) || 'workflow';
  const ver = version && version.trim() ? `_${version.trim().replace(/[^\w.-]+/g, '_')}` : '';
  return `${slug}${ver}.json`;
}
