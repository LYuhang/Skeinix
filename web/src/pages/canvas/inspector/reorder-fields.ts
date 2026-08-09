import type { FieldsMap } from './FieldsEditor';

export function reorderFields(fields: FieldsMap, from: number, to: number): FieldsMap {
  const names = Object.keys(fields);
  if (from === to || from < 0 || to < 0 || from >= names.length || to >= names.length) return fields;
  const ordered = [...names];
  const [moved] = ordered.splice(from, 1);
  ordered.splice(to, 0, moved);
  return Object.fromEntries(ordered.map((key) => [key, fields[key]]));
}
