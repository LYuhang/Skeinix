import { beforeEach, describe, expect, it, vi } from 'vitest';

let captured: any = null;
vi.mock('@microsoft/fetch-event-source', () => ({
  fetchEventSource: vi.fn(async (_url: string, opts: any) => {
    captured = opts;
    await opts.onopen?.({
      ok: true,
      status: 200,
      headers: { get: (name: string) => name === 'X-Turn-Id' ? 'n_header_1' : null },
    });
  }),
}));

import { streamNodeExecution } from '@/lib/api/sse/node-exec-stream';
import { useNodeExecStore } from '@/stores/node-exec';

beforeEach(() => {
  captured = null;
  useNodeExecStore.getState().reset();
});

describe('streamNodeExecution', () => {
  it('exposes the durable id and preserves cancelled across control-frame close', async () => {
    const onExecutionStarted = vi.fn();
    await streamNodeExecution({
      wfId: 'wf_1',
      nodeId: 'node_1',
      node: { node_id: 'node_1', node_type: 'CodeNode' },
      input: {},
      ac: new AbortController(),
      onExecutionStarted,
    });

    expect(onExecutionStarted).toHaveBeenCalledTimes(1);
    expect(onExecutionStarted).toHaveBeenCalledWith('n_header_1');

    captured.onmessage({
      event: 'EXEC_UPDATE',
      data: '{"node_id":"node_1","status":"running"}',
    });
    captured.onmessage({
      event: 'EXEC_UPDATE',
      data: '{"node_id":"node_1","status":"cancelled"}',
    });
    captured.onmessage({
      event: 'error',
      data: '{"code":"cancelled","message":"Turn cancelled by client."}',
    });

    expect(useNodeExecStore.getState().status).toBe('cancelled');
  });
});
