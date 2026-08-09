/**
 * Tabular text parsers shared by Batch input flows.
 *
 * `parseTabular(content, content_type)` normalises text into
 *     the SAME `{ columns, rows }` shape the modal's `parseCsv` produces, so
 *     a `/data` pick feeds the existing column-mapping → `submitBatch` flow
 *     unchanged. The batch route (`POST /workflows/{id}/batch`) is
 *     **inline-only** (`data_source.rows`, no VFS-path mode), so we fetch the
 *     bytes client-side and submit them inline — no backend change.
 */
import { parseDelimitedTable } from '@/lib/files/delimited';

export interface ParsedTable {
  columns: string[];
  rows: Record<string, string>[];
}

/**
 * Parse a `/data` file's inline text into `{ columns, rows }` (string cells),
 * matching the modal's `parseCsv` output so both sources feed one flow.
 *
 *   - `table/csv`  → comma-delimited (delegates to the supplied `csvParse`,
 *                    which the modal passes as its own `parseCsv`).
 *   - `table/tsv`  → tab-delimited (same minimal parser, tab separator).
 *   - `table/jsonl`→ one JSON object per line; the column set is the union of
 *                    all object keys (first-seen order); every cell is
 *                    stringified (non-string scalars → `String(v)`, objects →
 *                    `JSON.stringify`) since the batch contract is rows of
 *                    string cells.
 */
export function parseTabular(
  content: string,
  contentType: string,
  csvParse: (text: string) => ParsedTable,
): ParsedTable {
  if (contentType === 'table/csv') return csvParse(content);
  if (contentType === 'table/tsv') return parseDelimitedTable(content, '\t');
  if (contentType === 'table/jsonl') return parseJsonl(content);
  // Unknown — best-effort treat as CSV so we never hand back nothing.
  return csvParse(content);
}

function parseJsonl(text: string): ParsedTable {
  const lines = text
    .replace(/\r\n?/g, '\n')
    .split('\n')
    .map((l) => l.trim())
    .filter((l) => l.length > 0);
  const columns: string[] = [];
  const seen = new Set<string>();
  const rows: Record<string, string>[] = [];
  for (const line of lines) {
    let obj: unknown;
    try {
      obj = JSON.parse(line);
    } catch {
      continue; // skip malformed lines rather than crash the whole pick
    }
    if (obj === null || typeof obj !== 'object' || Array.isArray(obj)) continue;
    const rec = obj as Record<string, unknown>;
    const r: Record<string, string> = {};
    for (const [k, v] of Object.entries(rec)) {
      if (!seen.has(k)) {
        seen.add(k);
        columns.push(k);
      }
      r[k] =
        v === null || v === undefined
          ? ''
          : typeof v === 'object'
            ? JSON.stringify(v)
            : String(v);
    }
    rows.push(r);
  }
  // Backfill missing columns so every row has the full key set (string cells).
  for (const r of rows) {
    for (const c of columns) if (!(c in r)) r[c] = '';
  }
  return { columns, rows };
}
