import { beforeEach, describe, expect, it, vi } from 'vitest';

let captured: any = null;
vi.mock('@microsoft/fetch-event-source', () => ({
  fetchEventSource: vi.fn(async (_url: string, opts: any) => {
    captured = opts;
    await opts.onopen?.({ ok: true, status: 200 });
  }),
}));

import { fetchEventSource } from '@microsoft/fetch-event-source';
import { streamExecution } from '@/lib/api/sse/exec-stream';
import { useExecStreamStore } from '@/stores/exec-stream';

const nextAnimationFrame = () =>
  new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));

beforeEach(() => {
  captured = null;
  useExecStreamStore.getState().reset();
  (fetchEventSource as unknown as ReturnType<typeof vi.fn>).mockClear();
});

describe('streamExecution', () => {
  it('routes workflow EXEC_UPDATE frames into the exec stream store by node_id', async () => {
    const ac = new AbortController();
    await streamExecution({ wfId: 'wf_1', input: {}, ac });
    expect(captured).not.toBeNull();

    captured.onmessage({ id: '1', event: 'started', data: '{"turn_id":"wf_1"}' });
    captured.onmessage({
      id: '2',
      event: 'EXEC_UPDATE',
      data: '{"wf_id":"wf_1","node_id":"node_1","status":"running"}',
    });
    await nextAnimationFrame();
    expect(useExecStreamStore.getState().perNode.node_1.status).toBe('running');

    captured.onmessage({
      id: '3',
      event: 'EXEC_UPDATE',
      data: '{"wf_id":"wf_1","node_id":"node_1","status":"completed","result":"{\\"ok\\":true}"}',
    });
    captured.onmessage({
      id: '4',
      event: 'EXEC_UPDATE',
      data: '{"wf_id":"wf_1","node_id":"node_2","status":"running"}',
    });
    await nextAnimationFrame();

    const store = useExecStreamStore.getState();
    expect(store.perNode.node_1.status).toBe('completed');
    expect(store.perNode.node_1.result).toBe('{"ok":true}');
    expect(store.perNode.node_2.status).toBe('running');
  });

  it('closes a still-running workflow from the done control frame', async () => {
    const ac = new AbortController();
    await streamExecution({ wfId: 'wf_1', input: {}, ac });
    captured.onmessage({ id: '1', event: 'started', data: '{"turn_id":"wf_1"}' });
    captured.onmessage({
      id: '2',
      event: 'EXEC_UPDATE',
      data: '{"wf_id":"wf_1","node_id":"node_1","status":"running"}',
    });
    captured.onmessage({ id: '3', event: 'done', data: '{}' });

    const store = useExecStreamStore.getState();
    expect(store.status).toBe('completed');
    expect(store.perNode.node_1.status).toBe('completed');
  });
});
