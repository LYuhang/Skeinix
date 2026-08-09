/**
 * WorkflowCheckDialog renders a FORMATTED Check failure (headline + detail +
 * fix hint) instead of the raw terse engine string.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';

const mockCheck = {
  data: undefined as unknown,
  isPending: false,
  isError: false,
  error: undefined as unknown,
  reset: vi.fn(),
  mutateAsync: vi.fn().mockResolvedValue(undefined),
};

vi.mock('@/lib/api/mutations/workflow-ops', () => ({
  useCheckWorkflow: () => mockCheck,
}));

import { WorkflowCheckDialog } from '@/components/modals/WorkflowCheckDialog';
import { useWorkflowEditStore } from '@/stores/workflow-edit';

describe('WorkflowCheckDialog — formatted failure', () => {
  beforeEach(() => {
    mockCheck.isPending = false;
    mockCheck.isError = false;
    mockCheck.error = undefined;
    useWorkflowEditStore.getState().setDraft(null);
  });

  it('renders headline + detail + hint for a CodeNode process_fn failure', () => {
    mockCheck.data = {
      status: 'error',
      error_message:
        "[CodeNode Check]: The provided code must explicitly define a function named 'process_fn'.",
    };
    render(
      <WorkflowCheckDialog open onOpenChange={() => {}} wfId="wf_1" />,
    );
    expect(screen.getByTestId('check-fail-headline')).toHaveTextContent(
      'CodeNode Check',
    );
    expect(screen.getByTestId('check-fail-detail')).toHaveTextContent(
      /process_fn/,
    );
    expect(screen.getByTestId('check-fail-hint')).toHaveTextContent(
      /process_fn/,
    );
  });

  it('humanizes node ids in the failure to node_name(node_id)', () => {
    useWorkflowEditStore.getState().setDraft({
      node_3: {
        node_id: 'node_3',
        node_name: 'my_prompt',
        node_type: 'PromptNode',
        input_fields: {},
        output_fields: {},
        node_config: {},
        children: [],
      },
      __meta__: {},
    } as never);
    mockCheck.data = {
      status: 'error',
      error_message: '[PromptNode Check]: node_3 references a missing field.',
    };
    render(<WorkflowCheckDialog open onOpenChange={() => {}} wfId="wf_1" />);
    expect(screen.getByTestId('check-fail-detail')).toHaveTextContent(
      'my_prompt(node_3)',
    );
  });

  it('renders a generic headline when error_message is missing', () => {
    mockCheck.data = { status: 'error', error_message: null };
    render(
      <WorkflowCheckDialog open onOpenChange={() => {}} wfId="wf_1" />,
    );
    expect(screen.getByTestId('check-fail-headline')).toHaveTextContent(
      'Check failed',
    );
    expect(screen.queryByTestId('check-fail-hint')).toBeNull();
  });
});
