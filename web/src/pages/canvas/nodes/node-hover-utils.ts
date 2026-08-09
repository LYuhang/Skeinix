import type { NodeExecState } from './CustomNode';

export function hasNodeHoverContent(args: {
  description?: string;
  execState: NodeExecState;
  warnings: string[];
}): boolean {
  return Boolean(args.description?.trim()) || args.execState !== null || args.warnings.length > 0;
}
