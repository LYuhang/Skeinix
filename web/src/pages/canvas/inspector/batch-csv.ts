export interface ParsedCsv {
  columns: string[];
  rows: Record<string, string>[];
}

export function parseCsv(text: string): ParsedCsv {
  return parseDelimitedTable(text, ',');
}
import { parseDelimitedTable } from '@/lib/files/delimited';
