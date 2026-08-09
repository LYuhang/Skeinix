/**
 * Workflow normalization applied at the api send boundary (Check + Commit).
 *
 * The engine's node schema requires every `output_fields[*]` entry to carry a
 * `description` key (string). A field that reaches the draft WITHOUT that key —
 * e.g. agent-generated nodes, an imported workflow, or older data — makes
 * server-side Check fail with the cryptic
 *   "'description' is a required property, error_path: output_fields->…"
 * (the inspector's FieldsEditor already seeds `description: ''` for fields the
 * user adds, but other sources don't). We keep the schema strict (a description
 * SHOULD exist) and guarantee the key by backfilling an empty string for any
 * output field missing it, just before the workflow is sent to the backend.
 */

type Dict = Record<string, unknown>;

function isObject(x: unknown): x is Dict {
  return typeof x === 'object' && x !== null && !Array.isArray(x);
}

/**
 * Return a copy of `wf` where every node's `output_fields` entry has a
 * `description` key (defaulting to `''`). Pure — does not mutate the input.
 * Non-node entries (`__meta__`) and nodes without `output_fields` pass through
 * untouched. Unchanged nodes/fields are reused by reference (only the parts
 * that need a backfill are cloned).
 */
export function ensureOutputFieldDescriptions<T>(wf: T): T {
  if (!isObject(wf)) return wf;
  const out: Dict = {};
  for (const [nodeId, node] of Object.entries(wf)) {
    if (nodeId === '__meta__' || !isObject(node)) {
      out[nodeId] = node;
      continue;
    }
    const of = node.output_fields;
    if (!isObject(of)) {
      out[nodeId] = node;
      continue;
    }
    let changed = false;
    const nextFields: Dict = {};
    for (const [name, field] of Object.entries(of)) {
      if (isObject(field) && !('description' in field)) {
        nextFields[name] = { ...field, description: '' };
        changed = true;
      } else {
        nextFields[name] = field;
      }
    }
    out[nodeId] = changed ? { ...node, output_fields: nextFields } : node;
  }
  return out as T;
}

/**
 * The full send-boundary normalization applied before Check and Commit:
 * backfill output-field descriptions, PromptNode inference defaults,
 * HTTPRequestNode method/url defaults, then prune TransformNode orphan
 * mappings and migrate legacy Transform compute expressions. Pure — composes
 * the pure passes.
 */
export function normalizeForSend<T>(wf: T): T {
  return ensureTransformMappings(
    ensureHTTPRequestConfig(
      ensurePromptInferenceConfig(ensureOutputFieldDescriptions(wf)),
    ),
  );
}

/**
 * Return a copy of `wf` where every `TransformNode`'s `node_config.mappings`
 * drops any entry whose `output_field` is NOT a currently-declared output field
 * of that node. Orphan mappings are left behind when an output field is
 * renamed/removed — the editor only renders blocks for declared outputs, so the
 * orphan is invisible yet would still ship to the engine. The same pass
 * migrates legacy compute ops from `{ expression: ... }` to the canonical
 * `{ expr: ... }`. Everything else passes through untouched; unchanged nodes
 * are reused by reference. Pure — does not mutate the input.
 */
export function ensureTransformMappings<T>(wf: T): T {
  if (!isObject(wf)) return wf;
  const out: Dict = {};
  for (const [nodeId, node] of Object.entries(wf)) {
    if (nodeId === '__meta__' || !isObject(node) || node.node_type !== 'TransformNode') {
      out[nodeId] = node;
      continue;
    }
    const nc = isObject(node.node_config) ? node.node_config : {};
    if (!Array.isArray(nc.mappings)) {
      out[nodeId] = node;
      continue;
    }
    const declared = new Set(
      isObject(node.output_fields) ? Object.keys(node.output_fields) : [],
    );
    let changed = false;
    const kept = (nc.mappings as unknown[])
      .filter((m) => {
        const keep =
          isObject(m) &&
          typeof m.output_field === 'string' &&
          declared.has(m.output_field);
        if (!keep) changed = true;
        return keep;
      })
      .map((mapping) => {
        if (!isObject(mapping) || !Array.isArray(mapping.transform_list)) {
          return mapping;
        }
        let mappingChanged = false;
        const transformList = mapping.transform_list.map((op) => {
          if (
            isObject(op) &&
            op.op === 'compute' &&
            typeof op.expr !== 'string' &&
            typeof op.expression === 'string'
          ) {
            const { expression, ...rest } = op;
            mappingChanged = true;
            changed = true;
            return { ...rest, expr: expression };
          }
          return op;
        });
        return mappingChanged
          ? { ...mapping, transform_list: transformList }
          : mapping;
      });
    out[nodeId] =
      !changed
        ? node
        : { ...node, node_config: { ...nc, mappings: kept } };
  }
  return out as T;
}

/**
 * Engine-aligned PromptNode inference defaults (see CONFIG_SCHEMA in
 * `engine/.../nodes/prompt.py`: `inference_config` requires EXACTLY these four
 * keys). Kept in sync with the rows rendered by `PromptNodeEditor`.
 */
const PROMPT_INFERENCE_DEFAULTS: Dict = {
  temperature: 1.0,
  max_tokens: 512,
  top_k: -1,
  top_p: 0.9,
};

/**
 * Return a copy of `wf` where every `PromptNode`'s `node_config.inference_config`
 * carries the four required keys (`temperature`, `max_tokens`, `top_k`,
 * `top_p`), backfilling engine-aligned defaults for any that are absent. A
 * freshly-added PromptNode has `node_config: {}` (no `inference_config`), and a
 * user who never touches the inference rows leaves it absent — either way
 * server-side Check fails with "inference_config is a required property". The
 * editor only *renders* these defaults; this persists them at the send boundary.
 * Pure — unchanged nodes are reused by reference.
 */
export function ensurePromptInferenceConfig<T>(wf: T): T {
  if (!isObject(wf)) return wf;
  const out: Dict = {};
  for (const [nodeId, node] of Object.entries(wf)) {
    if (nodeId === '__meta__' || !isObject(node) || node.node_type !== 'PromptNode') {
      out[nodeId] = node;
      continue;
    }
    const nc = isObject(node.node_config) ? node.node_config : {};
    const ic = isObject(nc.inference_config) ? nc.inference_config : {};
    let changed = false;
    const nextIc: Dict = { ...ic };
    for (const [key, def] of Object.entries(PROMPT_INFERENCE_DEFAULTS)) {
      if (!(key in nextIc)) {
        nextIc[key] = def;
        changed = true;
      }
    }
    out[nodeId] = changed
      ? { ...node, node_config: { ...nc, inference_config: nextIc } }
      : node;
  }
  return out as T;
}

/**
 * Engine-aligned HTTPRequestNode config defaults (see CONFIG_SCHEMA in
 * `engine/.../nodes/http_request.py`: `node_config` `required: [method, url]`).
 * `method` is an enum (GET/POST/PUT/DELETE); `__call__` reads `config["method"]`
 * and `config["url"]` WITHOUT a default, so a missing key crashes the run with
 * "[HTTPRequestNode Call]: 'method'". `headers`/`body`/`auth`/`timeout` are all
 * optional (`.get()`), so we only backfill the two required keys.
 */
const HTTP_REQUEST_DEFAULTS: Dict = {
  method: 'GET',
  url: '',
};

/**
 * Return a copy of `wf` where every `HTTPRequestNode`'s `node_config` carries
 * the two required keys (`method`, `url`), backfilling engine-aligned defaults
 * for any that are absent. A freshly-added HTTPRequestNode has `node_config: {}`
 * (no `method`), and a user who never opens the editor leaves it absent — either
 * way server-side Check fails and `__call__` raises a `'method'` KeyError. The
 * editor only *renders* `method='GET'` as a fallback; this persists it at the
 * send boundary. Pure — unchanged nodes are reused by reference.
 */
export function ensureHTTPRequestConfig<T>(wf: T): T {
  if (!isObject(wf)) return wf;
  const out: Dict = {};
  for (const [nodeId, node] of Object.entries(wf)) {
    if (nodeId === '__meta__' || !isObject(node) || node.node_type !== 'HTTPRequestNode') {
      out[nodeId] = node;
      continue;
    }
    const nc = isObject(node.node_config) ? node.node_config : {};
    let changed = false;
    const nextNc: Dict = { ...nc };
    for (const [key, def] of Object.entries(HTTP_REQUEST_DEFAULTS)) {
      if (!(key in nextNc)) {
        nextNc[key] = def;
        changed = true;
      }
    }
    out[nodeId] = changed ? { ...node, node_config: nextNc } : node;
  }
  return out as T;
}
