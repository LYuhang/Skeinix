interface WorkbookCellLike {
  text?: string;
  value?: unknown;
}

export function workbookCellDisplayText(cell: WorkbookCellLike): string {
  if (cell.text) return cell.text;
  const value = cell.value;
  if (!value || typeof value !== 'object' || !('formula' in value)) return '';
  const formula = value.formula;
  if (typeof formula !== 'string' || !formula.trim()) return '';
  return `=${formula}`;
}
