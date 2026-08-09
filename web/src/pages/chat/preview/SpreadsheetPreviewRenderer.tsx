import { useEffect, useMemo, useState } from 'react';
import {
  AllCommunityModule,
  ModuleRegistry,
  themeQuartz,
  type ColDef,
} from 'ag-grid-community';
import { AgGridReact } from 'ag-grid-react';
import { useTranslation } from 'react-i18next';

import { Button } from '@/components/ui/button';
import {
  fileRefKey,
  type PreviewDescriptorV1,
  type PreviewErrorInfo,
} from '@/lib/preview/protocol';
import {
  parsePreviewTable,
  PREVIEW_TABLE_MAX_COLUMNS,
  PREVIEW_TABLE_MAX_ROWS,
  PREVIEW_WORKBOOK_MAX_SHEETS,
  PreviewTableError,
} from '@/lib/preview/table-parser';
import { PreviewErrorState } from './PreviewErrorState';
import type { PreviewRendererProps } from './renderer-types';

ModuleRegistry.registerModules([AllCommunityModule]);

type GridRow = Record<string, string>;

interface SheetData {
  name: string;
  columns: string[];
  rows: GridRow[];
}

type TableLoadState =
  | { key: string; status: 'loading' }
  | { key: string; status: 'ready'; sheets: SheetData[] }
  | { key: string; status: 'error'; error: PreviewErrorInfo };

function rowsToGrid(columns: string[], rows: string[][]): GridRow[] {
  return rows.map((row) => Object.fromEntries(
    columns.map((_name, index) => [`c${index}`, row[index] ?? '']),
  ));
}

async function loadDelimited(
  descriptor: PreviewDescriptorV1,
  signal: AbortSignal,
): Promise<{ sheets: SheetData[] }> {
  let content = descriptor.content?.inlineText;
  if (typeof content !== 'string') {
    const url = descriptor.content?.url;
    if (!url) throw new PreviewTableError('content_unavailable');
    const response = await fetch(url, { signal });
    if (!response.ok) throw new PreviewTableError('content_unavailable');
    content = await response.text();
  }
  const parsed = parsePreviewTable(content, descriptor.detectedType);
  return {
    sheets: [{
      name: descriptor.name,
      columns: parsed.columns,
      rows: rowsToGrid(parsed.columns, parsed.rows),
    }],
  };
}

async function loadWorkbook(
  descriptor: PreviewDescriptorV1,
  signal: AbortSignal,
): Promise<{ sheets: SheetData[] }> {
  const url = descriptor.content?.url;
  if (!url) throw new PreviewTableError('content_unavailable');
  const response = await fetch(url, { signal });
  if (!response.ok) throw new PreviewTableError('content_unavailable');
  const [buffer, ExcelJS] = await Promise.all([response.arrayBuffer(), import('exceljs')]);
  if (signal.aborted) throw new DOMException('Aborted', 'AbortError');
  const workbook = new ExcelJS.default.Workbook();
  try {
    await workbook.xlsx.load(buffer);
  } catch {
    throw new PreviewTableError('invalid_file');
  }
  if (workbook.worksheets.length > PREVIEW_WORKBOOK_MAX_SHEETS) {
    throw new PreviewTableError('too_many_sheets', {
      actual: workbook.worksheets.length,
      limit: PREVIEW_WORKBOOK_MAX_SHEETS,
    });
  }

  const sheets: SheetData[] = workbook.worksheets.map((worksheet) => {
    const columnCount = Math.max(worksheet.columnCount, 1);
    const dataRowCount = Math.max(0, worksheet.rowCount - 1);
    if (columnCount > PREVIEW_TABLE_MAX_COLUMNS) {
      throw new PreviewTableError('too_many_columns', {
        actual: columnCount,
        limit: PREVIEW_TABLE_MAX_COLUMNS,
        sheet: worksheet.name,
      });
    }
    if (dataRowCount > PREVIEW_TABLE_MAX_ROWS) {
      throw new PreviewTableError('too_many_rows', {
        actual: dataRowCount,
        limit: PREVIEW_TABLE_MAX_ROWS,
        sheet: worksheet.name,
      });
    }
    const columns = Array.from({ length: columnCount }, (_value, index) => {
      const text = worksheet.getRow(1).getCell(index + 1).text.trim();
      return text || `Column ${index + 1}`;
    });
    const rows = Array.from({ length: dataRowCount }, (_value, rowIndex) =>
      Object.fromEntries(columns.map((_name, columnIndex) => [
        `c${columnIndex}`,
        worksheet.getRow(rowIndex + 2).getCell(columnIndex + 1).text ?? '',
      ])),
    );
    return { name: worksheet.name, columns, rows };
  });
  return {
    sheets: sheets.length
      ? sheets
      : [{ name: 'Sheet1', columns: ['value'], rows: [] }],
  };
}

export function SpreadsheetPreviewRenderer({
  descriptor,
  loadAllowed,
}: PreviewRendererProps) {
  const { t } = useTranslation();
  const [activeSheet, setActiveSheet] = useState(0);
  const isTextTable = ['csv', 'tsv', 'jsonl'].includes(descriptor.detectedType);
  const loadKey = `${fileRefKey(descriptor.fileRef)}:${descriptor.revision}`;
  const [loadState, setLoadState] = useState<TableLoadState>({
    key: loadKey,
    status: 'loading',
  });
  const currentLoadState: TableLoadState = loadState.key === loadKey
    ? loadState
    : { key: loadKey, status: 'loading' };
  const sheets = currentLoadState.status === 'ready' ? currentLoadState.sheets : [];

  useEffect(() => {
    if (!loadAllowed) return;
    const controller = new AbortController();
    const request = isTextTable
      ? loadDelimited(descriptor, controller.signal)
      : loadWorkbook(descriptor, controller.signal);
    void request.then((result) => {
      setLoadState({ key: loadKey, status: 'ready', sheets: result.sheets });
      setActiveSheet(0);
    }).catch((reason) => {
      if (!controller.signal.aborted) {
        setLoadState({
          key: loadKey,
          status: 'error',
          error: reason instanceof PreviewTableError
            ? reason.details
            : { code: 'render_failed', params: {} },
        });
      }
    });
    return () => controller.abort();
  }, [descriptor, isTextTable, loadAllowed, loadKey]);

  const sheet = sheets[activeSheet];
  const columnDefs = useMemo<ColDef<GridRow>[]>(() => (sheet?.columns ?? []).map(
    (name, index) => ({
      field: `c${index}`,
      headerName: name,
      sortable: true,
      filter: true,
      resizable: true,
      minWidth: 110,
    }),
  ), [sheet?.columns]);

  if (!loadAllowed) return null;
  if (currentLoadState.status === 'loading') {
    return (
      <div className="p-4 text-sm text-muted-foreground">
        {t('preview.table.loading', 'Loading table…')}
      </div>
    );
  }
  if (currentLoadState.status === 'error') {
    return <PreviewErrorState descriptor={descriptor} error={currentLoadState.error} />;
  }
  if (!sheet) {
    return (
      <PreviewErrorState
        descriptor={descriptor}
        error={{ code: 'invalid_file', params: {} }}
      />
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex min-h-10 shrink-0 items-center gap-1 border-b border-edge-subtle px-2">
        {sheets.length > 1 ? sheets.map((item, index) => (
          <Button
            key={`${item.name}:${index}`}
            size="sm"
            variant={index === activeSheet ? 'secondary' : 'ghost'}
            onClick={() => setActiveSheet(index)}
          >
            {item.name}
          </Button>
        )) : <span className="px-2 text-xs text-muted-foreground">{sheet.name}</span>}
        <div className="flex-1" />
        <span className="text-xs text-muted-foreground">
          {t('preview.table.readOnly', 'Read only')}
        </span>
      </div>
      <div className="min-h-0 flex-1">
        <AgGridReact<GridRow>
          theme={themeQuartz}
          rowData={sheet.rows}
          columnDefs={columnDefs}
          defaultColDef={{ flex: 1 }}
          animateRows={false}
        />
      </div>
    </div>
  );
}
