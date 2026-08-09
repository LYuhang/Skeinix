/**
 * Tabular renderer for `table/jsonl`, `table/csv`, and `table/tsv`.
 *
 * Parses `output.data` into `{ columns, rows }`:
 *   - jsonl : one JSON object per line → columns = ordered union of keys.
 *   - csv   : comma-delimited; first row = header.
 *   - tsv   : tab-delimited; first row = header.
 *
 * Renders a bordered table, **capped at 20 rows inline** with a "View full
 * (N rows)" affordance that reuses the shared `ViewFullPanel` / `useVfsFull`
 * to lazy-load the full body from `output.path`. Narrow container → a
 * vertical key/value fallback (one card per row) for readability on the
 * sidebar's tight width.
 *
 * Fail-soft: if the body cannot be parsed into a non-empty table we fall back
 * to rendering it via `TextBlock` (never crash, always show something).
 */
import { useMemo, useRef, useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import type { ToolEnvelopeOutput } from './parseEnvelope';
import { TextBlock } from './TextBlock';
import { ViewFullPanel } from './ViewFullPanel';
import { parseTable } from './renderer-utils';

export interface TableViewProps {
  output: ToolEnvelopeOutput;
  abstract: string;
  status: 'success' | 'error';
  wfId: string | undefined;
}

/** Inline row cap before the "View full" affordance kicks in. */
export const INLINE_ROW_CAP = 20;

/** Narrow-container threshold (px) below which we use the vertical fallback. */
const NARROW_WIDTH_PX = 360;

export function TableView({ output, abstract, status, wfId }: TableViewProps) {
  const { t } = useTranslation();
  const containerRef = useRef<HTMLDivElement>(null);
  const [narrow, setNarrow] = useState(false);

  // Observe container width → vertical key/value fallback on narrow widths.
  useEffect(() => {
    const el = containerRef.current;
    if (!el || typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver((entries) => {
      const w = entries[0]?.contentRect.width ?? 0;
      setNarrow(w > 0 && w < NARROW_WIDTH_PX);
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const data = typeof output.data === 'string' ? output.data : undefined;
  const table = useMemo(
    () => parseTable(data, output.content_type),
    [data, output.content_type],
  );

  // Fail-soft: unparseable (or large-omitted with no data) → TextBlock.
  if (!table) {
    return (
      <TextBlock output={output} abstract={abstract} status={status} wfId={wfId} />
    );
  }

  const totalRows = table.rows.length;
  const capped = totalRows > INLINE_ROW_CAP;
  const visibleRows = capped ? table.rows.slice(0, INLINE_ROW_CAP) : table.rows;

  return (
    <div ref={containerRef} className="space-y-1" data-role="table-view">
      {narrow ? (
        <div className="space-y-1.5" data-role="table-vertical">
          {visibleRows.map((row, ri) => (
            <div
              key={ri}
              className="rounded border bg-muted/30 p-1.5 text-xs"
            >
              {table.columns.map((col, ci) => (
                <div key={ci} className="flex gap-2">
                  <span className="min-w-0 shrink-0 font-medium text-muted-foreground">
                    {col}
                  </span>
                  <span className="min-w-0 break-words">{row[ci]}</span>
                </div>
              ))}
            </div>
          ))}
        </div>
      ) : (
        <div className="max-h-72 overflow-auto rounded border">
          <table className="w-full border-collapse text-xs">
            <thead className="sticky top-0 bg-muted/60">
              <tr>
                {table.columns.map((col, ci) => (
                  <th
                    key={ci}
                    className="border-b border-r px-2 py-1 text-left font-semibold last:border-r-0"
                  >
                    {col}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {visibleRows.map((row, ri) => (
                <tr key={ri} className="even:bg-muted/20">
                  {table.columns.map((_, ci) => (
                    <td
                      key={ci}
                      className="border-b border-r px-2 py-1 align-top last:border-r-0"
                    >
                      {row[ci]}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {capped && (
        <div
          className="text-xs text-muted-foreground"
          data-role="table-cap-notice"
        >
          {t('tool.table_capped', { shown: INLINE_ROW_CAP, total: totalRows })}
        </div>
      )}
      {output.path && (
        <ViewFullPanel
          wfId={wfId}
          path={output.path}
          render={(content) => (
            <pre className="max-h-96 overflow-auto whitespace-pre-wrap break-words rounded border bg-muted/40 p-2 font-mono text-xs leading-snug">
              {content}
            </pre>
          )}
        />
      )}
    </div>
  );
}
