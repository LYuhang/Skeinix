/**
 * Browser-side Excel (.xlsx/.xls) parsing for the batch-run input.
 *
 * The column-mapping UI needs the columns BEFORE submit, so an uploaded Excel
 * workbook is parsed in the browser (the same place CSV/TSV/JSONL are parsed)
 * into the row-dict shape the batch task consumes. The first row is the header.
 *
 * `exceljs` is dynamically imported so it only loads when the user actually
 * picks an Excel file — it stays out of the main canvas bundle.
 */

export interface ParsedExcel {
  /** Header column names (first row), in order. */
  columns: string[];
  /** Data rows keyed by column name (string cells, via each cell's display text). */
  rows: Record<string, string>[];
  /** All sheet names in the workbook (drives the read-sheet picker). */
  sheetNames: string[];
}

/**
 * Parse one sheet of an Excel workbook into header + row dicts.
 *
 * @param buf       the workbook bytes (File.arrayBuffer()).
 * @param sheetName which sheet to read; defaults to the first sheet.
 */
export async function parseExcel(
  buf: ArrayBuffer,
  sheetName?: string,
): Promise<ParsedExcel> {
  const ExcelJS = (await import('exceljs')).default;
  const wb = new ExcelJS.Workbook();
  await wb.xlsx.load(buf);
  const sheetNames = wb.worksheets.map((ws) => ws.name);
  const ws = sheetName ? wb.getWorksheet(sheetName) : wb.worksheets[0];
  if (!ws) return { columns: [], rows: [], sheetNames };

  // Header from row 1 — includeEmpty keeps column alignment for the data rows.
  const columns: string[] = [];
  ws.getRow(1).eachCell({ includeEmpty: true }, (cell, col) => {
    columns[col - 1] = String(cell.text ?? '').trim();
  });

  const rows: Record<string, string>[] = [];
  ws.eachRow({ includeEmpty: false }, (row, rowNumber) => {
    if (rowNumber === 1) return; // header
    const r: Record<string, string> = {};
    columns.forEach((c, idx) => {
      if (!c) return; // skip unnamed columns
      const cell = row.getCell(idx + 1);
      r[c] = cell.text != null ? String(cell.text) : '';
    });
    rows.push(r);
  });

  return { columns: columns.filter(Boolean), rows, sheetNames };
}
