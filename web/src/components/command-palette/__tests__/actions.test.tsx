/**
 * Stream 4 — command-palette action registry.
 *
 * Two concerns:
 *   1. The `save` action forwards to the REAL toolbar affordance
 *      (`data-action="canvas-save"`), not the stale `save` that never
 *      existed.
 *   2. The clipboard / delete actions call the edit store DIRECTLY (there
 *      is no toolbar button to click), reading the live selection from the
 *      xyflow DOM (`.react-flow__node.selected[data-id]`).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { NavigateFunction } from 'react-router';
import { ACTIONS, type Action } from '@/components/command-palette/actions';
import { useWorkflowEditStore } from '@/stores/workflow-edit';
import { useUIStore } from '@/stores/ui';

function action(id: string): Action {
  const a = ACTIONS.find((x) => x.id === id);
  if (!a) throw new Error(`no action ${id}`);
  return a;
}

const ctx = { navigate: vi.fn() as unknown as NavigateFunction, wfId: 'wf_1' };

function selectNode(id: string): void {
  const el = document.createElement('div');
  el.className = 'react-flow__node selected';
  el.setAttribute('data-id', id);
  document.body.appendChild(el);
}

beforeEach(() => {
  useWorkflowEditStore.setState({ clipboard: [] });
  useWorkflowEditStore.getState().setDraft({
    node_1: { node_id: 'node_1', node_type: 'StartNode', children: ['node_2'] },
    node_2: {
      node_id: 'node_2',
      node_type: 'EndNode',
      children: [],
      __attributes__: { x: 10, y: 20 },
    },
  });
});

afterEach(() => {
  document.body.innerHTML = '';
});

describe('save action', () => {
  it('clicks the canvas-save toolbar affordance', () => {
    const btn = document.createElement('button');
    btn.setAttribute('data-action', 'canvas-save');
    const clicked = vi.fn();
    btn.addEventListener('click', clicked);
    document.body.appendChild(btn);

    action('save').handler(ctx);
    expect(clicked).toHaveBeenCalledOnce();
  });
});

describe('check action', () => {
  it('fires requestCheck (NOT a DOM click on the unmounted ⋯ item)', () => {
    useUIStore.setState({ checkRequestId: 0 });
    // A `[data-action="check"]` button that would catch a stray DOM-click.
    const btn = document.createElement('button');
    btn.setAttribute('data-action', 'check');
    const clicked = vi.fn();
    btn.addEventListener('click', clicked);
    document.body.appendChild(btn);

    action('check').handler(ctx);

    expect(useUIStore.getState().checkRequestId).toBe(1);
    expect(clicked).not.toHaveBeenCalled();
  });
});

describe('clipboard / delete actions', () => {
  it('copy-node loads the clipboard from the selected node', () => {
    selectNode('node_2');
    action('copy-node').handler(ctx);
    expect(useWorkflowEditStore.getState().clipboard).toHaveLength(1);
  });

  it('paste-node inserts a duplicate from the clipboard', () => {
    useWorkflowEditStore.setState({
      clipboard: [{ node_id: 'node_2', node_type: 'EndNode', children: [] }],
    });
    action('paste-node').handler(ctx);
    const draft = useWorkflowEditStore.getState().draft as Record<string, any>;
    const keys = Object.keys(draft).filter((k) => /^node_\d+$/.test(k));
    expect(keys).toHaveLength(3);
  });

  it('delete-selection removes the selected node', () => {
    selectNode('node_2');
    action('delete-selection').handler(ctx);
    const draft = useWorkflowEditStore.getState().draft as Record<string, any>;
    expect(draft.node_2).toBeUndefined();
  });
});
