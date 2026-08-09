/**
 * Client-side workflow search filter for the `/workspace` list.
 *
 * Matches a query (case-insensitive substring) against BOTH the workflow's
 * display name and its `wf_id`, so users can paste an id straight from the
 * canvas/explorer and find the row. An empty / whitespace-only query returns
 * the list unchanged. Extracted into its own module so the matching rule stays
 * pure + unit-testable, independent of the page's render/query plumbing.
 */
import type { components } from '@/lib/api/schema';

type WorkflowMetaOut = components['schemas']['WorkflowMetaOut'];

export function filterWorkflows<
  T extends Pick<WorkflowMetaOut, 'wf_id' | 'workflow_name'> &
    Partial<Pick<WorkflowMetaOut, 'description'>>,
>(
  items: T[],
  query: string,
): T[] {
  const q = query.trim().toLowerCase();
  if (!q) return items;
  return items.filter((wf) => {
    const name = (wf.workflow_name ?? '').toLowerCase();
    const id = (wf.wf_id ?? '').toLowerCase();
    const description = (wf.description ?? '').toLowerCase();
    return name.includes(q) || id.includes(q) || description.includes(q);
  });
}
