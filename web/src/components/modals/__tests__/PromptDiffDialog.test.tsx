/**
 * Integration regression test for the Prompt/Code History diff dialog.
 *
 * Reproduces the "stuck on Loading…" bug: the dialog opens, reads the version
 * list (real backend shape) + lazily fetches each version's snapshot, and must
 * resolve to a rendered diff (old vs current) rather than hang on the
 * `prompt_history.loading` placeholder forever.
 */
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';

// --- Mock the workflow queries module -------------------------------------
const fetchQueryMock = vi.fn();
const useWorkflowVersionsMock = vi.fn();

vi.mock('@/lib/api/queries/workflow', () => ({
  useWorkflowVersions: (...args: unknown[]) => useWorkflowVersionsMock(...args),
  // workflowAtQuery is passed to fetchQuery; we only need the key/fn shape so
  // the dialog's `{ ...workflowAtQuery(...), staleTime, retry }` spread works.
  workflowAtQuery: (wfId: string, v: number, sv: number) => ({
    queryKey: ['workflow-at', wfId, v, sv],
    queryFn: async () => ({}),
  }),
}));

// Stable client instance — the REAL useQueryClient returns a stable instance,
// so reproduce that (a fresh object each render would churn the useCallback
// deps and mask the real bug).
const stableClient = { fetchQuery: fetchQueryMock };
vi.mock('@tanstack/react-query', () => ({
  useQueryClient: () => stableClient,
}));

import { PromptDiffDialog } from '../PromptDiffDialog';

const VERSIONS = [
  { version_id: 'wf1_v1_sv0', version_str: 'v1.0', v: 1, sv: 0, major: 1, sub: 0 },
  { version_id: 'wf1_v1_sv1', version_str: 'v1.1', v: 1, sv: 1, major: 1, sub: 1 },
];

describe('PromptDiffDialog (history diff)', () => {
  beforeEach(() => {
    fetchQueryMock.mockReset();
    useWorkflowVersionsMock.mockReset();
    useWorkflowVersionsMock.mockReturnValue({
      data: { versions: VERSIONS },
      isLoading: false,
      isError: false,
      isSuccess: true,
    });
    // Each version snapshot: the backend `/at` endpoint returns
    // WorkflowSnapshotOut = { workflow, meta }.
    // sv1 (newest) == current → skipped; sv0 differs → resolve lands on it,
    // so the LEFT label is deterministically `v1.sv0`.
    fetchQueryMock.mockImplementation(async (opts: { queryKey: unknown[] }) => {
      const [, , , sv] = opts.queryKey as [string, string, number, number];
      const value = sv === 0 ? 'old prompt body' : 'new prompt body';
      return {
        workflow: {
          node_1: { node_config: { prompt_template: value } },
        },
        meta: {},
      };
    });
  });

  it('resolves to a diff instead of staying on Loading…', async () => {
    render(
      <PromptDiffDialog
        open
        onOpenChange={() => {}}
        wfId="wf1"
        nodeId="node_1"
        currentPrompt="new prompt body"
        field="prompt_template"
      />,
    );

    // It must NOT remain on the loading placeholder — the version stepper
    // label resolves to the historical version (`v1.sv0`), not "Loading…".
    await waitFor(() => {
      expect(screen.queryByText('Loading…')).not.toBeInTheDocument();
    });
    const versionLabelEl = document.querySelector(
      '[data-role="prompt-diff-version"]',
    );
    // The label resolved away from the "Loading…" placeholder (the i18n
    // template isn't interpolated under the bare test renderer, so we only
    // assert it's no longer the loading string).
    expect(versionLabelEl?.textContent ?? '').not.toMatch(/Loading/i);

    // The diff is rendered (react-diff-viewer splits text across cells, so we
    // assert on the container's combined text rather than a single node).
    await waitFor(() => {
      const diff = document.querySelector('[data-role="prompt-diff"]');
      expect(diff).toBeTruthy();
      const diffText = (diff?.textContent ?? '').replace(/\s+/g, ' ');
      // LEFT = historical "old prompt body", RIGHT = current "new prompt body".
      expect(diffText).toContain('old');
      expect(diffText).toContain('new');
      expect(diffText).toContain('prompt body');
    });
  });

  it('does not crash on close when the version list collapses with a stale leftIndex', async () => {
    // On close the dialog calls useWorkflowVersions(undefined) → the query is
    // disabled and `versions` collapses to [] BEFORE the reset effect nulls
    // leftIndex. A stale leftIndex then read `versions[leftIndex].major` →
    // "Cannot read properties of undefined (reading 'major')" crashed the page.
    useWorkflowVersionsMock.mockImplementation((wfId?: string) =>
      wfId
        ? { data: { versions: VERSIONS }, isLoading: false, isError: false, isSuccess: true }
        : { data: undefined, isLoading: false, isError: false, isSuccess: false },
    );

    const props = {
      onOpenChange: () => {},
      wfId: 'wf1',
      nodeId: 'node_1',
      currentPrompt: 'new prompt body',
      field: 'prompt_template',
    };
    const { rerender } = render(<PromptDiffDialog open {...props} />);
    await waitFor(() => {
      expect(screen.queryByText('Loading…')).not.toBeInTheDocument();
    });

    // Closing must render without throwing.
    expect(() => rerender(<PromptDiffDialog open={false} {...props} />)).not.toThrow();
  });
});
