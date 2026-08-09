/**
 * Execution-stream store: the canvas-highlighting source of truth.
 *
 * Covers the slices the per-node ring rendering relies on:
 *   - Path A per-node frames land `running`/`completed`/`error` into
 *     `perNode[nodeId].status` verbatim (the backend's wire values).
 *   - `begin()` wipes `perNode` (a new run starts from a clean canvas) and
 *     flips overall `status` to `running`.
 *   - `setStatus()` drives the overall status the toolbar reads (Execute
 *     disable / Cancel swap).
 *   - `reset()` clears BOTH `perNode` and overall `status` → idle, so stale
 *     green/red rings die when the wfId changes or the page unmounts.
 */
import { beforeEach, describe, expect, it } from 'vitest';
import { useExecStreamStore } from '@/stores/exec-stream';

const get = () => useExecStreamStore.getState();

beforeEach(() => {
  get().reset();
});

describe('exec-stream store — per-node frames (Path A)', () => {
  it('lands running → completed status verbatim per node', () => {
    get().applyUpdate({ node_id: 'node_1', status: 'running' });
    expect(get().perNode.node_1.status).toBe('running');

    get().applyUpdate({ node_id: 'node_1', status: 'completed', result: 'ok' });
    expect(get().perNode.node_1.status).toBe('completed');
    expect(get().perNode.node_1.result).toBe('ok');
  });

  it('lands an error status + error message per node', () => {
    get().applyUpdate({ node_id: 'node_2', status: 'running' });
    get().applyUpdate({ node_id: 'node_2', status: 'error', error: 'boom' });
    expect(get().perNode.node_2.status).toBe('error');
    expect(get().perNode.node_2.error).toBe('boom');
  });

  it('keeps per-node frames independent (one node does not clobber another)', () => {
    get().applyUpdate({ node_id: 'node_1', status: 'completed' });
    get().applyUpdate({ node_id: 'node_2', status: 'running' });
    expect(get().perNode.node_1.status).toBe('completed');
    expect(get().perNode.node_2.status).toBe('running');
  });

  it('lands a per-node duration from the completed frame', () => {
    get().applyUpdate({ node_id: 'node_1', status: 'running' });
    // The running frame has no duration yet.
    expect(get().perNode.node_1.duration).toBeUndefined();
    get().applyUpdate({
      node_id: 'node_1',
      status: 'completed',
      result: 'ok',
      duration: 0.42,
    });
    expect(get().perNode.node_1.duration).toBe(0.42);
    // result is still preserved alongside the duration.
    expect(get().perNode.node_1.result).toBe('ok');
  });

  it('preserves a prior per-node duration when a later frame omits it', () => {
    get().applyUpdate({ node_id: 'node_1', status: 'completed', duration: 1.5 });
    get().applyUpdate({ node_id: 'node_1', status: 'completed', result: 'late' });
    expect(get().perNode.node_1.duration).toBe(1.5);
  });

  it('ignores frames for a different workflow id', () => {
    get().begin('wf_1', new AbortController());
    get().applyUpdate({ wf_id: 'wf_2', node_id: 'node_1', status: 'running' });
    expect(get().perNode).toEqual({});

    get().applyUpdate({ wf_id: 'wf_1', node_id: 'node_1', status: 'running' });
    expect(get().perNode.node_1.status).toBe('running');
  });
});

describe('exec-stream store — end-to-end total duration (Path B terminal)', () => {
  it('captures the terminal frame duration into totalDuration', () => {
    expect(get().totalDuration).toBeNull();
    get().applyUpdate({ status: 'completed', outputs: { y: 1 }, duration: 1.23 });
    expect(get().totalDuration).toBe(1.23);
  });

  it('captures total even when the terminal frame has no outputs/errors', () => {
    get().applyUpdate({ status: 'completed', duration: 2.34 });
    expect(get().totalDuration).toBe(2.34);
  });

  it('begin() and reset() clear totalDuration', () => {
    get().applyUpdate({ status: 'completed', duration: 9.9 });
    expect(get().totalDuration).toBe(9.9);
    get().begin('wf_1', new AbortController());
    expect(get().totalDuration).toBeNull();

    get().applyUpdate({ status: 'completed', duration: 5.5 });
    expect(get().totalDuration).toBe(5.5);
    get().reset();
    expect(get().totalDuration).toBeNull();
  });

  // Regression: the terminal data frame is authoritative for the OVERALL run
  // status — the store must NOT depend solely on the separate `done` control
  // event (some SSE clients don't dispatch the trailing event before the
  // stream closes, which left finished runs stuck on "running").
  it('flips overall status to completed from the terminal frame (no done event)', () => {
    get().begin('wf_1', new AbortController());
    expect(get().status).toBe('running');
    // Terminal fence frame: status, outputs, no node_id, NO done event after.
    get().applyUpdate({ status: 'completed', outputs: { result: 'ok' }, duration: 0.2 });
    expect(get().status).toBe('completed');
  });

  it('closes any still-running node when the workflow terminal frame is completed', () => {
    get().begin('wf_1', new AbortController());
    get().applyUpdate({ node_id: 'node_1', status: 'running' });

    get().applyUpdate({ status: 'completed', duration: 0.2 });

    expect(get().status).toBe('completed');
    expect(get().perNode.node_1.status).toBe('completed');
  });

  it('flips overall status to error from the terminal frame', () => {
    get().begin('wf_1', new AbortController());
    get().applyUpdate({
      status: 'error',
      errors: { node_2: { status: 'error', error_message: 'boom' } },
      duration: 0.3,
    });
    expect(get().status).toBe('error');
    // The node error still expands onto its per-node card.
    expect(get().perNode.node_2.status).toBe('error');
    expect(get().perNode.node_2.error).toBe('boom');
  });

  it('does not present a terminal frame with errors as completed', () => {
    get().begin('wf_1', new AbortController());
    get().applyUpdate({
      status: 'error',
      errors: { __engine__: { status: 'error', error_message: 'invalid input' } },
      duration: 0,
    });

    expect(get().status).toBe('error');
    expect(get().perNode.__engine__).toMatchObject({
      status: 'error',
      error: 'invalid input',
    });
  });

  it('flips overall status to cancelled and closes running node rings from a terminal frame', () => {
    get().begin('wf_1', new AbortController());
    get().applyUpdate({ node_id: 'node_1', status: 'completed' });
    get().applyUpdate({ node_id: 'node_2', status: 'running' });

    get().applyUpdate({ status: 'cancelled' });

    expect(get().status).toBe('cancelled');
    expect(get().perNode.node_1.status).toBe('completed');
    expect(get().perNode.node_2.status).toBe('cancelled');
    expect(get().abortController).toBeNull();
  });

  it('does not regress overall status on a per-node frame (Path A)', () => {
    get().begin('wf_1', new AbortController());
    get().applyUpdate({ node_id: 'node_1', status: 'completed' });
    // A single node completing must NOT mark the whole run completed.
    expect(get().status).toBe('running');
  });
});

describe('exec-stream store — lifecycle', () => {
  it('begin() wipes perNode + flips status to running', () => {
    get().applyUpdate({ node_id: 'node_1', status: 'completed' });
    const ac = new AbortController();
    get().begin('wf_1', ac);
    expect(get().perNode).toEqual({});
    expect(get().status).toBe('running');
    expect(get().wfId).toBe('wf_1');
    expect(get().abortController).toBe(ac);
  });

  it('setStatus() drives the overall status', () => {
    get().setStatus('running');
    expect(get().status).toBe('running');
    get().setStatus('completed');
    expect(get().status).toBe('completed');
  });

  it('setStatus(cancelled) closes currently running nodes', () => {
    get().begin('wf_1', new AbortController());
    get().applyUpdate({ node_id: 'node_1', status: 'completed' });
    get().applyUpdate({ node_id: 'node_2', status: 'running' });

    get().setStatus('cancelled');

    expect(get().status).toBe('cancelled');
    expect(get().perNode.node_1.status).toBe('completed');
    expect(get().perNode.node_2.status).toBe('cancelled');
    expect(get().abortController).toBeNull();
  });

  it('reset() clears perNode AND status (kills stale rings)', () => {
    get().begin('wf_1', new AbortController());
    get().applyUpdate({ node_id: 'node_1', status: 'completed' });
    get().setStatus('completed');

    get().reset();

    expect(get().perNode).toEqual({});
    expect(get().status).toBe('idle');
    expect(get().wfId).toBeNull();
    expect(get().abortController).toBeNull();
  });
});
