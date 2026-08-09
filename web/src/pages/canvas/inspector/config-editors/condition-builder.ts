/**
 * Pure helpers for the Condition structured builder (Stream 3 amendment).
 *
 * The engine evaluates each `condition_str` as a Python expression AFTER
 * substituting every `{field}` placeholder with `repr(input_value)`
 * (`condition.py:__call__`). So `{field}` references the ConditionNode's
 * OWN `input_fields` (those keys are what `inputs` carries at runtime) —
 * NOT arbitrary upstream outputs. The builder therefore offers the node's
 * own input-field names as the field dropdown (with a Start-inputs
 * fallback for a node that has not declared inputs yet).
 *
 * `buildConditionStr` turns (field, operator, value) into a valid Python
 * expression. The value literal is rendered Python-side:
 *   - a bare number → numeric literal (`0.8`)
 *   - `true`/`false` → `True`/`False`
 *   - anything else → a single-quoted string literal (quotes escaped)
 *
 * `contains` / `in` map to Python membership:
 *   - `contains` → `<value> in {field}`  (the field collection holds value)
 *   - `in`       → `{field} in <value>`  (the field is one of value)
 */

export const CONDITION_OPERATORS = [
  '==',
  '!=',
  '>',
  '>=',
  '<',
  '<=',
  'contains',
  'in',
] as const;

export type ConditionOperator = (typeof CONDITION_OPERATORS)[number];

/** Render a raw value string as a Python literal for the generated expression. */
export function toPyLiteral(raw: string): string {
  const trimmed = raw.trim();
  if (trimmed === '') return "''";
  if (/^-?\d+(\.\d+)?$/.test(trimmed)) return trimmed; // number
  const lower = trimmed.toLowerCase();
  if (lower === 'true') return 'True';
  if (lower === 'false') return 'False';
  // string literal — escape backslashes then single quotes.
  const escaped = trimmed.replace(/\\/g, '\\\\').replace(/'/g, "\\'");
  return `'${escaped}'`;
}

/** Generate a `condition_str` from the structured (field, op, value) triple. */
export function buildConditionStr(
  field: string,
  operator: ConditionOperator,
  value: string,
): string {
  if (!field) return '';
  const placeholder = `{${field}}`;
  const literal = toPyLiteral(value);
  if (operator === 'contains') return `${literal} in ${placeholder}`;
  if (operator === 'in') return `${placeholder} in ${literal}`;
  return `${placeholder} ${operator} ${literal}`;
}
