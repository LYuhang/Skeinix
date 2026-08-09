/**
 * Pure, fail-soft parser for the agent tool-result *envelope*.
 *
 * Every well-formed agent tool returns its result as a JSON string with this
 * public envelope shape:
 *
 * ```jsonc
 * {
 *   "status": "success" | "error",
 *   "error": null | "…",
 *   "abstract": "plain-language one-liner",
 *   "output": {
 *     "path": "/exec/cmd_2.txt",     // VFS handle for the full body
 *     "content_type": "text/shell",  // RENDER HINT
 *     "data": "…head+tail…",         // inline body OR omitted when large
 *     "full_chars": 151893,          // present when data omitted
 *     "full_tokens": 37973,          // present when data omitted
 *     "exit_code": 0, "stderr": "…"  // tool-specific extras
 *   }
 * }
 * ```
 *
 * `result` on the wire is the JSON *string*. This module turns it into a
 * typed object — or returns `null` when the string is not a parseable
 * envelope (legacy plain-string results, or any JSON missing `status`), so
 * callers fall back to the legacy `<pre>` rendering. It never throws.
 */

export interface ToolEnvelopeOutput {
  path?: string;
  content_type?: string;
  data?: string;
  full_chars?: number;
  full_tokens?: number;
  exit_code?: number;
  stderr?: string;
  /** Tolerate forward-compatible extras without losing them. */
  [key: string]: unknown;
}

export interface ToolEnvelope {
  status: 'success' | 'error';
  error: string | null;
  abstract: string;
  output?: ToolEnvelopeOutput;
}

function isObject(x: unknown): x is Record<string, unknown> {
  return typeof x === 'object' && x !== null;
}

/**
 * Parse a tool result string into a `ToolEnvelope`, or `null` when it is not
 * a recognisable envelope. Recognition is deliberately lenient on every
 * field EXCEPT `status` (the one field that all envelopes carry and that no
 * legacy plain-string result would have once JSON-parsed): a value is an
 * envelope iff it JSON-parses to an object whose `status` is
 * `"success"` or `"error"`.
 */
export function parseEnvelope(result: string | undefined): ToolEnvelope | null {
  if (typeof result !== 'string' || result.length === 0) return null;

  let parsed: unknown;
  try {
    parsed = JSON.parse(result);
  } catch {
    return null;
  }

  if (!isObject(parsed)) return null;
  const status = parsed.status;
  if (status !== 'success' && status !== 'error') return null;

  const error =
    typeof parsed.error === 'string' ? parsed.error : null;
  const abstract =
    typeof parsed.abstract === 'string' ? parsed.abstract : '';

  let output: ToolEnvelopeOutput | undefined;
  if (isObject(parsed.output)) {
    // Pass the output object through verbatim (extras preserved); the typed
    // fields are read defensively by the renderers, so no per-field coercion
    // is required here.
    output = parsed.output as ToolEnvelopeOutput;
  }

  return { status, error, abstract, output };
}

/** Normalize the current Runtime-neutral product artifact into the same
 * presenter envelope used by historical string-embedded results. */
export function parseArtifactEnvelope(
  artifact: Record<string, unknown> | null | undefined,
): ToolEnvelope | null {
  if (!isObject(artifact)) return null;
  const status = artifact.status;
  if (status !== 'success' && status !== 'error') return null;
  const meta = isObject(artifact.meta) ? artifact.meta : {};
  const artifactBody = isObject(artifact.artifact) ? artifact.artifact : {};
  const target = isObject(artifactBody.target) ? artifactBody.target : {};
  const handles = isObject(artifactBody.handles) ? artifactBody.handles : {};
  const payload = isObject(artifact.payload) ? artifact.payload : {};
  const errorValue = artifact.error;
  const error = typeof errorValue === 'string'
    ? errorValue
    : isObject(errorValue)
      ? String(errorValue.message || errorValue.code || '') || null
      : null;
  const output: ToolEnvelopeOutput = {
    ...handles,
    content_type: typeof meta.content_type === 'string' ? meta.content_type : undefined,
    data: typeof artifact.content === 'string' ? artifact.content : undefined,
    path: typeof target.path === 'string'
      ? target.path
      : typeof payload.ref === 'string'
        ? payload.ref
        : undefined,
  };
  if (isObject(payload.size)) {
    if (typeof payload.size.chars === 'number') output.full_chars = payload.size.chars;
    if (typeof payload.size.tokens === 'number') output.full_tokens = payload.size.tokens;
  }
  return {
    status,
    error,
    abstract: typeof artifact.content_abstract === 'string'
      ? artifact.content_abstract
      : '',
    output,
  };
}

/**
 * True when the backend OMITTED the inline body because it was large: there
 * is no `output.data`, but a size hint (`full_tokens` / `full_chars`) is
 * present. Renderers use this to show a "Large output — View full" stub
 * instead of an empty pane.
 */
export function isLargeOmitted(env: ToolEnvelope | null): boolean {
  const out = env?.output;
  if (!out) return false;
  const noData = out.data === undefined || out.data === null;
  const hasSizeHint =
    typeof out.full_tokens === 'number' || typeof out.full_chars === 'number';
  return noData && hasSizeHint;
}
