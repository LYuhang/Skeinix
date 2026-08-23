import type { MergedToolCall } from './types';

export function workflowIdFromToolCall(call: MergedToolCall): string | null {
  const artifact = call.artifact;
  const nested = artifact?.artifact;
  const handles = nested && typeof nested === 'object' && !Array.isArray(nested)
    ? (nested as { handles?: unknown }).handles
    : null;
  if (handles && typeof handles === 'object' && !Array.isArray(handles)) {
    const id = (handles as { workflow_id?: unknown }).workflow_id;
    if (typeof id === 'string' && id) return id;
  }
  const payload = artifact?.payload;
  if (payload && typeof payload === 'object' && !Array.isArray(payload)) {
    const id = (payload as { workflow_id?: unknown }).workflow_id;
    if (typeof id === 'string' && id) return id;
  }
  try {
    const parsed = call.result ? JSON.parse(call.result) : null;
    if (parsed && typeof parsed === 'object') {
      const value = parsed as { workflow_id?: unknown; meta?: { workflow_id?: unknown } };
      if (typeof value.workflow_id === 'string') return value.workflow_id;
      if (typeof value.meta?.workflow_id === 'string') return value.meta.workflow_id;
    }
  } catch {
    // Legacy plain-text results carry no workflow id.
  }
  return null;
}
