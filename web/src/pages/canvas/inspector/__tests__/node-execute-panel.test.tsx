/**
 * NodeExecutePanel is the node's own debug-execution surface.
 *
 * The panel reads the selected node's draft `input_fields`, renders a
 * preset-only input form, Run drives an injected SSE runner (so no network
 * mock), the spinner shows while running, the output log fills from the
 * dedicated `useNodeExecStore`, and Stop aborts.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, cleanup, act, waitFor, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

// Controllable xyflow selection (mirrors node-tab-fields.test.tsx).
const selection = { nodes: [] as { id: string; selected: boolean; data: unknown }[] };
vi.mock('@xyflow/react', () => ({
  useNodes: () => selection.nodes,
}));

// The panel reads node results from the workflow's stable run tier (runId=wfId).
// The panel renders WITHOUT a QueryClient here, so mock the query hook to return
// staged data from mutable cells.
const persisted: {
  /** Keyed by nodeId → the parsed run-file `{inputs, output, status, error}`. */
  fileByNode: Record<string, unknown>;
  lastRunId?: string | null;
} = { fileByNode: {}, lastRunId: null };
vi.mock('@/lib/api/queries/vfs', () => ({
  useRunNodeResult: (runId: string | null | undefined, nodeId: string) => {
    persisted.lastRunId = runId ?? null;
    return runId ? persisted.fileByNode[nodeId] : undefined;
  },
}));

import { NodeExecutePanel } from '@/pages/canvas/inspector/NodeExecutePanel';
import { useWorkflowEditStore } from '@/stores/workflow-edit';
import { useNodeExecStore } from '@/stores/node-exec';
import { useExecStreamStore } from '@/stores/exec-stream';
import type { StreamNodeExecutionArgs } from '@/lib/api/sse/node-exec-stream';

function seed(draft: Record<string, unknown>, selectedId: string) {
  useWorkflowEditStore.getState().setDraft(draft);
  const node = draft[selectedId] as Record<string, unknown>;
  selection.nodes = [{ id: selectedId, selected: true, data: node }];
}

const codeNode = {
  node_1: {
    node_id: 'node_1',
    node_type: 'CodeNode',
    node_name: 'code',
    input_fields: { x: { type: 'string', value: 'hi', reference: '' } },
    output_fields: {},
    node_config: {},
  },
};

beforeEach(() => {
  useNodeExecStore.getState().reset();
  // reset() keeps the per-node input buffer; clear it so a prior test's
  // persisted debug inputs don't bleed into the next render.
  useNodeExecStore.setState({ inputsByNode: {} });
  useExecStreamStore.getState().reset();
  persisted.fileByNode = {};
  persisted.lastRunId = null;
  useWorkflowEditStore.setState({
    draft: null,
    baseline: 'null',
    undoStack: [],
    redoStack: [],
  });
  selection.nodes = [];
});
afterEach(() => cleanup());

describe('NodeExecutePanel', () => {
  it('prompts to select a node when none is selected', () => {
    render(<NodeExecutePanel wfId="wf_1" />);
    expect(screen.getByText(/select a node to run it/i)).toBeInTheDocument();
  });

  it('renders the input form for the selected node', () => {
    seed(codeNode, 'node_1');
    render(<NodeExecutePanel wfId="wf_1" runner={vi.fn()} />);
    expect(screen.getByTestId('node-execute-panel')).toBeInTheDocument();
    // The field label + preset widget render (x, type string).
    expect(screen.getByText(/^x$/)).toBeInTheDocument();
    expect(screen.getByTestId('node-exec-x-value')).toBeInTheDocument();
    // No reference toggle — references are disabled for node-debug.
    expect(screen.queryByTestId('node-exec-x-ref-toggle')).toBeNull();
  });

  it('Run ships the draft node + raw inputs and shows running → output', async () => {
    seed(codeNode, 'node_1');
    let captured: StreamNodeExecutionArgs | null = null;
    // A runner that flips the store like the real SSE client would.
    const runner = vi.fn(async (args: StreamNodeExecutionArgs) => {
      captured = args;
      useNodeExecStore.getState().begin(args.nodeId, args.ac, args.wfId);
      useNodeExecStore.getState().applyUpdate({
        node_id: args.nodeId,
        status: 'running',
      });
    });

    render(<NodeExecutePanel wfId="wf_1" runner={runner} />);
    await userEvent.click(screen.getByTestId('node-exec-run'));

    expect(runner).toHaveBeenCalledTimes(1);
    expect(captured!.wfId).toBe('wf_1');
    expect(captured!.nodeId).toBe('node_1');
    // The draft node_dict is shipped (M2).
    expect((captured!.node as Record<string, unknown>).node_type).toBe('CodeNode');
    // Input shipped from the editable field buffer; backend normalizes it.
    expect(captured!.input).toEqual({ x: 'hi' });

    // Spinner + Stop appear while running.
    expect(screen.getByTestId('node-exec-spinner')).toBeInTheDocument();
    expect(screen.getByTestId('node-exec-stop')).toBeInTheDocument();
    expect(screen.getByTestId('node-exec-status').textContent).toBe('running');

    // A terminal completed frame fills the output log.
    act(() => {
      useNodeExecStore.getState().applyUpdate({
        node_id: 'node_1',
        status: 'completed',
        result: '{"y":"hi!"}',
      });
    });
    expect(screen.getByTestId('node-exec-status').textContent).toBe('completed');
    expect(screen.getByTestId('node-exec-result').textContent).toContain('"y":"hi!"');
    // Spinner gone, Run re-enabled.
    expect(screen.queryByTestId('node-exec-spinner')).toBeNull();
    expect(screen.getByTestId('node-exec-run')).not.toBeDisabled();
  });

  it('ships list/object-like node debug inputs as raw strings before running', async () => {
    seed(
      {
        node_1: {
          node_id: 'node_1',
          node_type: 'CodeNode',
          node_name: 'code',
          input_fields: {
            image_urls: { type: 'list', value: '', reference: '' },
            meta: { type: 'dict', value: '', reference: '' },
          },
          output_fields: {},
          node_config: {},
        },
      },
      'node_1',
    );
    let captured: StreamNodeExecutionArgs | null = null;
    const runner = vi.fn(async (args: StreamNodeExecutionArgs) => {
      captured = args;
      useNodeExecStore.getState().begin(args.nodeId, args.ac, args.wfId);
    });

    render(<NodeExecutePanel wfId="wf_1" runner={runner} />);
    fireEvent.change(screen.getByTestId('node-exec-image_urls-json'), {
      target: { value: '["u1","u2"]' },
    });
    fireEvent.change(screen.getByTestId('node-exec-meta-json'), {
      target: { value: '{"source":"test"}' },
    });
    await userEvent.click(screen.getByTestId('node-exec-run'));

    expect(runner).toHaveBeenCalledTimes(1);
    const submitted = captured as StreamNodeExecutionArgs | null;
    expect(submitted?.input?.image_urls).toBe('["u1","u2"]');
    expect(typeof submitted?.input?.image_urls).toBe('string');
    expect(submitted?.input?.meta).toBe('{"source":"test"}');
  });

  it('titles the panel by node_name (node_id), not node_type', () => {
    seed(codeNode, 'node_1');
    render(<NodeExecutePanel wfId="wf_1" runner={vi.fn()} />);
    // node_name = 'code' (NOT the type 'CodeNode'); id in parens. Text spans
    // multiple nodes (verb + name + span), so assert on the title container.
    const title = screen.getByTestId('node-exec-title');
    expect(title.textContent).toContain('code');
    expect(title.textContent).not.toContain('CodeNode');
    expect(title.textContent).toContain('(node_1)');
  });

  it('Format toggles structured output to indent=2 and Restore reverts', async () => {
    seed(codeNode, 'node_1');
    const runner = vi.fn(async (args: StreamNodeExecutionArgs) => {
      useNodeExecStore.getState().begin(args.nodeId, args.ac, args.wfId);
    });
    render(<NodeExecutePanel wfId="wf_1" runner={runner} />);
    await userEvent.click(screen.getByTestId('node-exec-run'));
    act(() => {
      useNodeExecStore.getState().applyUpdate({
        node_id: 'node_1',
        status: 'completed',
        result: '{"y":"hi!"}',
      });
    });

    // Raw: compact, no space after the colon.
    const pre = screen.getByTestId('node-exec-result');
    expect(pre.textContent).toContain('{"y":"hi!"}');
    const toggle = screen.getByTestId('node-exec-format-toggle');
    expect(toggle.textContent).toBe('Format');

    // Format → indented (space after colon, multi-line), button flips.
    await userEvent.click(toggle);
    expect(pre.textContent).toContain('"y": "hi!"');
    expect(screen.getByTestId('node-exec-format-toggle').textContent).toBe('Restore');

    // Restore → back to the raw compact string.
    await userEvent.click(screen.getByTestId('node-exec-format-toggle'));
    expect(pre.textContent).toContain('{"y":"hi!"}');
    expect(screen.getByTestId('node-exec-format-toggle').textContent).toBe('Format');
  });

  it('hides the Format toggle for non-structured output', async () => {
    seed(codeNode, 'node_1');
    const runner = vi.fn(async (args: StreamNodeExecutionArgs) => {
      useNodeExecStore.getState().begin(args.nodeId, args.ac, args.wfId);
    });
    render(<NodeExecutePanel wfId="wf_1" runner={runner} />);
    await userEvent.click(screen.getByTestId('node-exec-run'));
    act(() => {
      useNodeExecStore.getState().applyUpdate({
        node_id: 'node_1',
        status: 'completed',
        result: 'plain text, not json',
      });
    });
    expect(screen.getByTestId('node-exec-result').textContent).toContain('plain text');
    expect(screen.queryByTestId('node-exec-format-toggle')).toBeNull();
  });

  it('surfaces an error frame in the output log', async () => {
    seed(codeNode, 'node_1');
    const runner = vi.fn(async (args: StreamNodeExecutionArgs) => {
      useNodeExecStore.getState().begin(args.nodeId, args.ac, args.wfId);
      useNodeExecStore.getState().applyUpdate({
        node_id: args.nodeId,
        status: 'error',
        error: 'boom',
      });
    });
    render(<NodeExecutePanel wfId="wf_1" runner={runner} />);
    await userEvent.click(screen.getByTestId('node-exec-run'));

    expect(screen.getByTestId('node-exec-status').textContent).toBe('error');
    expect(screen.getByTestId('node-exec-error').textContent).toContain('boom');
  });

  it('Stop requests server cancellation and waits for its cancelled frame', async () => {
    seed(codeNode, 'node_1');
    const abort = vi.fn();
    const canceller = vi.fn(async () => undefined);
    const runner = vi.fn(async (args: StreamNodeExecutionArgs) => {
      args.ac.abort = abort as unknown as AbortController['abort'];
      useNodeExecStore.getState().begin(args.nodeId, args.ac, args.wfId);
      useNodeExecStore.getState().applyUpdate({
        node_id: args.nodeId,
        status: 'running',
      });
      args.onExecutionStarted?.('n_server_1');
    });
    render(
      <NodeExecutePanel
        wfId="wf_1"
        runner={runner}
        canceller={canceller}
      />,
    );
    await userEvent.click(screen.getByTestId('node-exec-run'));
    await userEvent.click(screen.getByTestId('node-exec-stop'));

    expect(canceller).toHaveBeenCalledWith('n_server_1');
    expect(abort).not.toHaveBeenCalled();
    expect(screen.getByTestId('node-exec-status').textContent).toBe('running');

    act(() => {
      useNodeExecStore.getState().applyUpdate({
        node_id: 'node_1',
        status: 'cancelled',
      });
    });
    expect(screen.getByTestId('node-exec-status').textContent).toBe('cancelled');
  });
});

describe('NodeExecutePanel — last workflow-run autofill', () => {
  // Seed the exec-stream store with a finished workflow run for THIS workflow,
  // capturing node_1's resolved inputs + output.
  function seedRun(wfId: string, nodeId: string, inputs: unknown, result: string) {
    const store = useExecStreamStore.getState();
    store.begin(wfId, new AbortController());
    store.applyUpdate({ node_id: nodeId, status: 'completed', inputs, result });
  }

  it('prefills inputs from the last workflow run for this node', () => {
    seedRun('wf_1', 'node_1', { x: 'from-run' }, '{"y":42}');
    seed(codeNode, 'node_1');
    render(<NodeExecutePanel wfId="wf_1" runner={vi.fn()} />);
    // The x input widget shows the run's resolved value, not the configured 'hi'.
    expect((screen.getByTestId('node-exec-x-input') as HTMLInputElement).value).toBe(
      'from-run',
    );
  });

  it('shows the last workflow run output for this node', () => {
    seedRun('wf_1', 'node_1', { x: 'from-run' }, '{"y":42}');
    seed(codeNode, 'node_1');
    render(<NodeExecutePanel wfId="wf_1" runner={vi.fn()} />);
    expect(screen.getByTestId('node-exec-lastrun')).toBeInTheDocument();
    expect(screen.getByTestId('node-exec-lastrun-result').textContent).toContain(
      '"y":42',
    );
  });

  it('ignores a run that belongs to a different workflow', () => {
    seedRun('wf_OTHER', 'node_1', { x: 'from-run' }, '{"y":42}');
    seed(codeNode, 'node_1');
    render(<NodeExecutePanel wfId="wf_1" runner={vi.fn()} />);
    // No cross-workflow bleed: input keeps the configured default, no last-run box.
    expect((screen.getByTestId('node-exec-x-input') as HTMLInputElement).value).toBe(
      'hi',
    );
    expect(screen.queryByTestId('node-exec-lastrun')).toBeNull();
  });

  it('dedups the output boxes: a node-debug run supersedes the last workflow run', async () => {
    // A whole-workflow run produced a result for node_1...
    seedRun('wf_1', 'node_1', { x: 'from-run' }, '{"y":42}');
    seed(codeNode, 'node_1');
    // ...AND the user re-ran node_1 in isolation (node-debug), which completed.
    const runner = vi.fn(async (args: StreamNodeExecutionArgs) => {
      useNodeExecStore.getState().begin(args.nodeId, args.ac, args.wfId);
      useNodeExecStore.getState().applyUpdate({
        node_id: args.nodeId,
        status: 'completed',
        result: '{"y":99}',
      });
    });
    render(<NodeExecutePanel wfId="wf_1" runner={runner} />);
    await userEvent.click(screen.getByTestId('node-exec-run'));

    // Only the node-debug box shows; the last-workflow-run box is gated off.
    expect(screen.queryByTestId('node-exec-lastrun')).toBeNull();
    expect(screen.getByTestId('node-exec-log')).toBeInTheDocument();
    expect(screen.getByTestId('node-exec-result').textContent).toContain('"y":99');
  });
});

describe('NodeExecutePanel — run-file source (Task 5)', () => {
  // No live exec-stream data (store cleared on remount / reload), but the
  // workflow has a PERSISTED run whose `/run/__exec__/nodes/{id}.json` carries
  // the node's inputs + output. The panel must recover them from the FILE
  // WITHOUT writing to the global exec-stream store.
  it('prefills inputs + shows last-run output from the run file', async () => {
    // No live run seeded → liveHasInputs is false → useRunNodeResult reads
    // the workflow run tier keyed by wfId.
    persisted.fileByNode = {
      node_1: { status: 'completed', inputs: { x: 'from-run' }, output: { y: 1 } },
    };
    seed(codeNode, 'node_1');
    render(<NodeExecutePanel wfId="wf_1" runner={vi.fn()} />);

    // The file's inputs arrive after mount → the effect re-seeds the form field
    // (the mount seed had the configured 'hi').
    await screen.findByTestId('node-execute-panel');
    const input = await waitFor(() => {
      const el = screen.getByTestId('node-exec-x-input') as HTMLInputElement;
      expect(el.value).toBe('from-run');
      return el;
    });
    expect(input.value).toBe('from-run');

    // The Last-workflow-run section shows the file's output (`output` dict
    // JSON.stringify'd onto `result`).
    expect(screen.getByTestId('node-exec-lastrun')).toBeInTheDocument();
    expect(screen.getByTestId('node-exec-lastrun-status').textContent).toContain(
      'completed',
    );
    expect(screen.getByTestId('node-exec-lastrun-result').textContent).toContain(
      '"y":1',
    );
    expect(persisted.lastRunId).toBe('wf_1');
  });

  it('live store wins over the run file (no double-source)', () => {
    // A live run carries inputs → the run-file source must NOT be consulted.
    const store = useExecStreamStore.getState();
    store.begin('wf_1', new AbortController());
    store.applyUpdate({
      node_id: 'node_1',
      status: 'completed',
      inputs: { x: 'from-live' },
      result: '{"y":1}',
    });
    persisted.fileByNode = {
      node_1: { status: 'completed', inputs: { x: 'from-run' }, output: { y: 7 } },
    };
    seed(codeNode, 'node_1');
    render(<NodeExecutePanel wfId="wf_1" runner={vi.fn()} />);
    expect((screen.getByTestId('node-exec-x-input') as HTMLInputElement).value).toBe(
      'from-live',
    );
    expect(screen.getByTestId('node-exec-lastrun-result').textContent).toContain(
      '"y":1',
    );
  });
});

describe('NodeExecutePanel — TemplateNode Render toggle', () => {
  const templateNode = {
    node_1: {
      node_id: 'node_1',
      node_type: 'TemplateNode',
      node_name: 'tmpl',
      input_fields: {},
      output_fields: {},
      node_config: { output_format: 'text' },
    },
  };

  function seedRun(wfId: string, nodeId: string, result: string) {
    const store = useExecStreamStore.getState();
    store.begin(wfId, new AbortController());
    store.applyUpdate({ node_id: nodeId, status: 'completed', result });
  }

  it('renders the `rendered` field below the raw output when Render is clicked', async () => {
    seedRun('wf_1', 'node_1', '{"rendered":"hello world","format":"text"}');
    seed(templateNode, 'node_1');
    render(<NodeExecutePanel wfId="wf_1" runner={vi.fn()} />);

    // The raw dict shows in the last-run box; a Render toggle is present.
    expect(screen.getByTestId('node-exec-lastrun-result').textContent).toContain(
      '"rendered":"hello world"',
    );
    const toggle = screen.getByTestId('node-exec-lastrun-format-toggle-render');
    expect(toggle.textContent).toBe('Render');
    expect(screen.queryByTestId('rendered-preview')).toBeNull();

    // Click → the rendered text preview appears containing the rendered content.
    await userEvent.click(toggle);
    expect(screen.getByTestId('rendered-preview').textContent).toContain('hello world');
    expect(screen.getByTestId('node-exec-lastrun-format-toggle-render').textContent).toBe(
      'Hide',
    );
  });

  it('uses the format carried IN the output (markdown) over node_config', async () => {
    // node_config says 'text', but the output carries format:'markdown' — the
    // renderer must honour the output's format, rendering a markdown heading.
    seedRun('wf_1', 'node_1', '{"rendered":"## Title","format":"markdown"}');
    seed(templateNode, 'node_1');
    render(<NodeExecutePanel wfId="wf_1" runner={vi.fn()} />);

    await userEvent.click(
      screen.getByTestId('node-exec-lastrun-format-toggle-render'),
    );
    const preview = screen.getByTestId('rendered-preview');
    // react-markdown turns '## Title' into an <h2> (markdown path), not a <pre>.
    expect(preview.querySelector('h2')?.textContent).toBe('Title');
  });
});
