export interface ParsedDelimitedTable {
  columns: string[];
  rows: Record<string, string>[];
}

/**
 * Parse RFC 4180-style delimited text without splitting quoted fields.
 * Supports escaped quotes, CRLF/LF records, embedded delimiters/newlines, a
 * UTF-8 BOM, short rows, and an optional trailing record separator.
 */
export function parseDelimitedTable(
  text: string,
  delimiter: ',' | '\t' = ',',
): ParsedDelimitedTable {
  const records: string[][] = [];
  let record: string[] = [];
  let field = '';
  let quoted = false;

  const pushField = () => {
    record.push(field);
    field = '';
  };
  const pushRecord = () => {
    pushField();
    if (record.some((cell) => cell.length > 0)) records.push(record);
    record = [];
  };

  for (let index = 0; index < text.length; index += 1) {
    const character = text[index]!;
    if (quoted) {
      if (character === '"') {
        if (text[index + 1] === '"') {
          field += '"';
          index += 1;
        } else {
          quoted = false;
        }
      } else {
        field += character;
      }
      continue;
    }
    if (character === '"' && field.length === 0) {
      quoted = true;
    } else if (character === delimiter) {
      pushField();
    } else if (character === '\n') {
      pushRecord();
    } else if (character === '\r') {
      if (text[index + 1] === '\n') index += 1;
      pushRecord();
    } else {
      field += character;
    }
  }
  if (field.length > 0 || record.length > 0) pushRecord();
  if (records.length === 0) return { columns: [], rows: [] };

  const columns = records[0]!.map((cell, index) => {
    const normalized = index === 0 ? cell.replace(/^\uFEFF/, '') : cell;
    return normalized.trim();
  });
  const rows = records.slice(1).map((cells) => Object.fromEntries(
    columns.map((column, index) => [column, cells[index] ?? '']),
  ));
  return { columns, rows };
}
