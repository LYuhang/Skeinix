export type RendererKind = 'terminal' | 'diff' | 'code' | 'markdown' | 'table' | 'json' | 'html' | 'link' | 'text';

const CODE_TYPES = new Set(['text/python', 'text/x-python', 'application/x-python']);
const TABLE_TYPES = new Set(['table/jsonl', 'table/csv', 'table/tsv']);

export function rendererFor(contentType: string | undefined): RendererKind {
  const value = (contentType ?? '').toLowerCase();
  if (value === 'text/shell') return 'terminal';
  if (value === 'text/x-diff' || value === 'text/diff') return 'diff';
  if (CODE_TYPES.has(value)) return 'code';
  if (value === 'text/markdown') return 'markdown';
  if (TABLE_TYPES.has(value)) return 'table';
  if (value === 'application/json') return 'json';
  if (value === 'text/html') return 'html';
  if (value.startsWith('link/')) return 'link';
  return 'text';
}

export type ErrorCategory = 'timeout' | 'not_found' | 'permission' | 'bad_input' | 'unknown';

export function classifyError(error: string | null | undefined): ErrorCategory {
  const value = (error ?? '').toLowerCase();
  if (!value) return 'unknown';
  if (['timeout', 'timed out', 'deadline'].some((term) => value.includes(term))) return 'timeout';
  if (['not found', 'no such file', 'does not exist', 'enoent', '404'].some((term) => value.includes(term))) return 'not_found';
  if (['permission', 'denied', 'forbidden', 'not allowed', 'eacces', 'unauthorized', '403', '401'].some((term) => value.includes(term))) return 'permission';
  if (['invalid', 'bad request', 'malformed', 'validation', 'expected', 'required', '400', '422'].some((term) => value.includes(term))) return 'bad_input';
  return 'unknown';
}

export interface SubAgentResult {
  status: 'success' | 'error';
  output: unknown;
  error: string | null;
  reasoning_ref?: string;
}

export function subAgentFromResult(result: string | undefined): SubAgentResult | null {
  if (!result) return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(result);
  } catch {
    return null;
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return null;
  const value = parsed as Record<string, unknown>;
  if ((value.status !== 'success' && value.status !== 'error') || !('output' in value)) return null;
  return {
    status: value.status,
    output: value.output,
    error: typeof value.error === 'string' ? value.error : null,
    reasoning_ref: typeof value.reasoning_ref === 'string' ? value.reasoning_ref : undefined,
  };
}

export interface ParsedTable {
  columns: string[];
  rows: string[][];
}

function cellToString(value: unknown): string {
  if (value === null || value === undefined) return '';
  if (typeof value !== 'object') return String(value);
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function splitDelimited(line: string, delimiter: string): string[] {
  const output: string[] = [];
  let current = '';
  let quoted = false;
  for (let index = 0; index < line.length; index += 1) {
    const character = line[index];
    if (quoted && character === '"') {
      if (line[index + 1] === '"') {
        current += '"';
        index += 1;
      } else {
        quoted = false;
      }
    } else if (!quoted && character === '"') {
      quoted = true;
    } else if (!quoted && character === delimiter) {
      output.push(current);
      current = '';
    } else {
      current += character;
    }
  }
  output.push(current);
  return output;
}

export function parseTable(
  data: string | undefined,
  contentType: string | undefined,
): ParsedTable | null {
  if (typeof data !== 'string' || !data.trim()) return null;
  const type = (contentType ?? '').toLowerCase();
  if (type === 'table/jsonl') {
    const records: Record<string, unknown>[] = [];
    for (const raw of data.split('\n')) {
      if (!raw.trim()) continue;
      try {
        const value: unknown = JSON.parse(raw);
        if (value && typeof value === 'object' && !Array.isArray(value)) {
          records.push(value as Record<string, unknown>);
        }
      } catch {
        // Invalid lines are skipped; an entirely invalid payload fails soft.
      }
    }
    if (!records.length) return null;
    const columns = Array.from(new Set(records.flatMap((record) => Object.keys(record))));
    return { columns, rows: records.map((record) => columns.map((key) => cellToString(record[key]))) };
  }
  if (type !== 'table/csv' && type !== 'table/tsv') return null;
  const lines = data.split('\n').filter((line, index) => line.length > 0 || index === 0);
  while (lines.length > 1 && !lines.at(-1)?.trim()) lines.pop();
  if (!lines.length) return null;
  const delimiter = type === 'table/tsv' ? '\t' : ',';
  const columns = splitDelimited(lines[0], delimiter);
  if (!columns.length) return null;
  return {
    columns,
    rows: lines.slice(1).map((line) => {
      const cells = splitDelimited(line, delimiter);
      return columns.map((_, index) => cells[index] ?? '');
    }),
  };
}

export function exitCodeTone(
  exitCode: number | undefined,
  status: 'success' | 'error',
): 'green' | 'red' {
  if (typeof exitCode === 'number') return exitCode === 0 ? 'green' : 'red';
  return status === 'error' ? 'red' : 'green';
}
