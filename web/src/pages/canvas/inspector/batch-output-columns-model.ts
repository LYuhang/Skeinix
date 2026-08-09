import type { BatchOutputColumn } from '@/lib/api/tasks';

export interface UserColumn {
  id: string;
  name: string;
  node: string;
  field: string;
  default: string;
}

export const FIXED_KINDS = ['index', 'status', 'error', 'execution_time'] as const;
export type FixedKind = (typeof FIXED_KINDS)[number];

export interface BatchColumnsState {
  fixedNames: Record<FixedKind, string>;
  userColumns: UserColumn[];
}

let sequence = 0;

export function defaultColumnsState(): BatchColumnsState {
  return {
    fixedNames: {
      index: 'index',
      status: 'status',
      error: 'error',
      execution_time: 'execution_time',
    },
    userColumns: [],
  };
}

export function makeUserColumn(): UserColumn {
  sequence += 1;
  return { id: `col_${sequence}`, name: '', node: '', field: '', default: '' };
}

export function reorderUserColumns(
  columns: UserColumn[],
  from: number,
  to: number,
): UserColumn[] {
  if (from === to || from < 0 || to < 0 || from >= columns.length || to >= columns.length) {
    return columns;
  }
  const next = columns.slice();
  const [moved] = next.splice(from, 1);
  next.splice(to, 0, moved);
  return next;
}

export function toWireColumns(state: BatchColumnsState): BatchOutputColumn[] {
  const output: BatchOutputColumn[] = FIXED_KINDS.map((kind) => ({
    kind,
    name: state.fixedNames[kind].trim() || kind,
  }));
  for (const column of state.userColumns) {
    if (!column.node || !column.field) continue;
    const defaultValue = column.default.trim();
    output.push({
      kind: 'field',
      name: column.name.trim() || `${column.node}.${column.field}`,
      node: column.node,
      field: column.field,
      ...(defaultValue ? { default: defaultValue } : {}),
    });
  }
  return output;
}

export function encodeSource(node: string, field: string): string {
  return JSON.stringify([node, field]);
}

export function decodeSource(value: string): { node: string; field: string } {
  if (!value) return { node: '', field: '' };
  try {
    const parsed: unknown = JSON.parse(value);
    if (Array.isArray(parsed) && typeof parsed[0] === 'string' && typeof parsed[1] === 'string') {
      return { node: parsed[0], field: parsed[1] };
    }
  } catch {
    // Malformed selector values fail closed instead of creating partial config.
  }
  return { node: '', field: '' };
}
