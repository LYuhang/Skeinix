/**
 * Stream 7 — `data-files` unit tests (pure helpers).
 *
 * Covers the content-to-rows normaliser.
 */
import { describe, expect, it } from 'vitest';
import { parseTabular } from '@/lib/api/queries/data-files';

// The minimal CSV parser the modal passes in (mirror of `parseCsv`).
function csvParse(text: string) {
  const lines = text
    .replace(/\r\n?/g, '\n')
    .split('\n')
    .map((l) => l.trim())
    .filter((l) => l.length > 0);
  if (lines.length === 0) return { columns: [], rows: [] };
  const columns = lines[0].split(',').map((s) => s.trim());
  const rows = lines.slice(1).map((line) => {
    const cells = line.split(',');
    const r: Record<string, string> = {};
    columns.forEach((c, i) => {
      r[c] = (cells[i] ?? '').trim();
    });
    return r;
  });
  return { columns, rows };
}

describe('parseTabular', () => {
  it('csv → delegates to the supplied csvParse', () => {
    const out = parseTabular('a,b\n1,2\n', 'table/csv', csvParse);
    expect(out.columns).toEqual(['a', 'b']);
    expect(out.rows).toEqual([{ a: '1', b: '2' }]);
  });

  it('tsv → tab-delimited', () => {
    const out = parseTabular('a\tb\n1\t2\n', 'table/tsv', csvParse);
    expect(out.columns).toEqual(['a', 'b']);
    expect(out.rows).toEqual([{ a: '1', b: '2' }]);
  });

  it('tsv keeps quoted tabs and embedded newlines in one cell', () => {
    const out = parseTabular('a\tb\n"x\ty"\t"line 1\nline 2"\n', 'table/tsv', csvParse);
    expect(out.rows).toEqual([{ a: 'x\ty', b: 'line 1\nline 2' }]);
  });

  it('jsonl → object-per-line, union of keys, stringified cells', () => {
    const out = parseTabular(
      '{"a": "x", "b": 1}\n{"a": "y", "c": true}\n',
      'table/jsonl',
      csvParse,
    );
    expect(out.columns).toEqual(['a', 'b', 'c']);
    expect(out.rows).toEqual([
      { a: 'x', b: '1', c: '' },
      { a: 'y', b: '', c: 'true' },
    ]);
  });

  it('jsonl → skips malformed lines, stringifies nested objects', () => {
    const out = parseTabular(
      '{"a": 1}\nnot json\n{"a": {"k": 2}}\n',
      'table/jsonl',
      csvParse,
    );
    expect(out.columns).toEqual(['a']);
    expect(out.rows).toEqual([{ a: '1' }, { a: '{"k":2}' }]);
  });

  it('unknown type → best-effort CSV', () => {
    const out = parseTabular('a\n1\n', 'table/weird', csvParse);
    expect(out.columns).toEqual(['a']);
    expect(out.rows).toEqual([{ a: '1' }]);
  });
});
