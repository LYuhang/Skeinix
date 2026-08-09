// @vitest-environment node
// exceljs's zip/stream pipeline needs real Node streams — run this in the node
// environment (the default project env is jsdom, where exceljs hangs).
import { describe, it, expect } from 'vitest';
import ExcelJS from 'exceljs';
import { parseExcel } from '../excel';

async function workbook(): Promise<ArrayBuffer> {
  const wb = new ExcelJS.Workbook();
  const a = wb.addWorksheet('Alpha');
  a.addRow(['name', 'qty']);
  a.addRow(['apple', '3']);
  a.addRow(['pear', '7']);
  const b = wb.addWorksheet('Beta');
  b.addRow(['name']);
  b.addRow(['solo']);
  const buf = await wb.xlsx.writeBuffer();
  return buf as ArrayBuffer;
}

describe('parseExcel', () => {
  it('reads the first sheet by default (header + row dicts)', async () => {
    const { columns, rows, sheetNames } = await parseExcel(await workbook());
    expect(sheetNames).toEqual(['Alpha', 'Beta']);
    expect(columns).toEqual(['name', 'qty']);
    expect(rows).toEqual([
      { name: 'apple', qty: '3' },
      { name: 'pear', qty: '7' },
    ]);
  });

  it('reads a named sheet', async () => {
    const { columns, rows } = await parseExcel(await workbook(), 'Beta');
    expect(columns).toEqual(['name']);
    expect(rows).toEqual([{ name: 'solo' }]);
  });

  it('returns empty for an unknown sheet but still lists the sheet names', async () => {
    const { columns, rows, sheetNames } = await parseExcel(await workbook(), 'Nope');
    expect(columns).toEqual([]);
    expect(rows).toEqual([]);
    expect(sheetNames).toEqual(['Alpha', 'Beta']);
  });
});
