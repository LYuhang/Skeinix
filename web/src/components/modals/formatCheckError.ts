/**
 * Make a failed-`Workflow.check` `error_message` legible.
 *
 * The engine raises terse jsonschema / assertion strings. Common shapes
 * (verified against the engine):
 *   - `[StartNode Check]: ... 'node_name' ... 'const' was '__start__'` —
 *     a node was renamed off its reserved name (start.py/end.py `const`).
 *   - `... must explicitly define a function named 'process_fn'` /
 *     `'process_fn' is a required property` — CodeNode config (code.py +
 *     sandbox.py:393).
 *   - `... output_fields should have the same field names as input_fields ...`
 *     — Start/End mirror (start.py:143 / end.py:111).
 *   - `Workflow must contain exactly one StartNode, found N.`
 *   - `Found isolated nodes unreachable from StartNode: {...}` (workflow.py:83).
 *
 * This is NOT a parser — it splits the leading `[X Check]: ` prefix into a
 * headline, keeps the rest as readable detail, and appends a short
 * plain-language "what to fix" hint when the message matches a known shape.
 */

export interface FormattedCheckError {
  /** Short headline (the `[X Check]` prefix, or a generic fallback). */
  headline: string;
  /** The remaining message body, trimmed (may be empty). */
  detail: string;
  /** Optional plain-language fix hint for recognized error shapes. */
  hint?: string;
}

const PREFIX_RE = /^\s*\[([^\]]+)\]:\s*/;

/** Plain-language hints keyed by a substring match on the raw message. */
const HINT_RULES: ReadonlyArray<{ test: RegExp; hint: string }> = [
  {
    test: /process_fn/i,
    hint: "the Code node's body must define a function named exactly `process_fn(inputs)` that returns a dict.",
  },
  {
    test: /'?node_name'?.*(const|was '__start__'|was '__end__'|__start__|__end__)/i,
    hint: 'the Start node must be named `__start__` and the End node `__end__` — these names are reserved, restore them.',
  },
  {
    test: /output_fields.*input_fields|input_fields.*output_fields/i,
    hint: "this node's output fields must mirror its input fields (same names and types).",
  },
  {
    test: /exactly one StartNode/i,
    hint: 'a workflow needs exactly one Start node — add or remove one.',
  },
  {
    test: /isolated nodes|unreachable from StartNode/i,
    hint: 'every node must be reachable from the Start node — connect the listed node(s) into the flow.',
  },
  {
    test: /Directed Acyclic Graph|Cycle detected/i,
    hint: 'the connections form a loop — remove the edge that points back upstream.',
  },
  {
    test: /'condition'/i,
    hint: "a Condition node must have a single output field named `condition`.",
  },
];

export function formatCheckError(
  raw: string | null | undefined,
): FormattedCheckError {
  const message = (raw ?? '').trim();
  if (!message) {
    return { headline: 'Check failed', detail: '' };
  }

  const prefixMatch = message.match(PREFIX_RE);
  let headline = 'Check failed';
  let detail = message;
  if (prefixMatch) {
    headline = prefixMatch[1].trim();
    detail = message.slice(prefixMatch[0].length).trim();
  }

  const hint = HINT_RULES.find((r) => r.test.test(message))?.hint;

  return { headline, detail, hint };
}
