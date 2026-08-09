export type TransformOp = Record<string, unknown> & { op?: string };

export interface TransformMapping {
  input_field: string;
  output_field: string;
  transform_list: TransformOp[];
}

export const TRANSFORM_OP_TYPES = [
  'path',
  'index',
  'length',
  'cast',
  'default',
  'compute',
  'pick',
] as const;

export type TransformOpType = (typeof TRANSFORM_OP_TYPES)[number];

export function defaultTransform(type: TransformOpType): TransformOp {
  switch (type) {
    case 'path': return { op: 'path', path: '' };
    case 'index': return { op: 'index', index: 0 };
    case 'length': return { op: 'length' };
    case 'cast': return { op: 'cast', to: 'string' };
    case 'default': return { op: 'default', value: '' };
    case 'compute': return { op: 'compute', expr: '' };
    case 'pick': return { op: 'pick', fields: [] };
  }
}

export function moveTransform(
  list: TransformOp[],
  from: number,
  to: number,
): TransformOp[] {
  const next = [...list];
  if (from >= 0 && from < next.length && to >= 0 && to < next.length && from !== to) {
    const [moved] = next.splice(from, 1);
    next.splice(to, 0, moved);
  }
  return next;
}
