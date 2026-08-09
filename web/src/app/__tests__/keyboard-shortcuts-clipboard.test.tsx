/**
 * Stream 4 — KeyboardShortcuts clipboard + delete bindings.
 *
 * The global window handler (mounted outside the canvas ReactFlowProvider)
 * reads the live selection from the xyflow DOM (`.react-flow__node.selected`
 * / `.react-flow__edge.selected`, both carrying a `data-id`). These tests
 * seed those DOM markers + the edit-store draft directly, dispatch a window
 * keydown, and assert the draft mutated (or did NOT, under the guards).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, fireEvent } from '@testing-library/react';
import { KeyboardShortcuts } from '@/app/KeyboardShortcuts';
import { useWorkflowEditStore } from '@/stores/workflow-edit';
import { useUIStore } from '@/stores/ui';

type Wf = Record<string, any>;

function seedDraft(): void {
  useWorkflowEditStore.getState().setDraft({
    node_1: {
      node_id: 'node_1',
      node_type: 'StartNode',
      node_name: 'start',
      children: ['node_2'],
      __attributes__: { x: 100, y: 200 },
    },
    node_2: {
      node_id: 'node_2',
      node_type: 'EndNode',
      node_name: 'end',
      children: [],
      __attributes__: { x: 300, y: 400 },
    },
  });
}

/** Render a fake xyflow node element with `selected` + the given data-id. */
function selectNode(id: string): void {
  const el = document.createElement('div');
  el.className = 'react-flow__node selected';
  el.setAttribute('data-id', id);
  document.body.appendChild(el);
}

function selectEdge(source: string, target: string): void {
  const el = document.createElement('div');
  el.className = 'react-flow__edge selected';
  el.setAttribute('data-id', `${source}->${target}`);
  document.body.appendChild(el);
}

function press(
  key: string,
  opts: { meta?: boolean; shift?: boolean; target?: HTMLElement } = {},
): void {
  const target = opts.target ?? document.body;
  fireEvent.keyDown(target, {
    key,
    metaKey: opts.meta ?? false,
    ctrlKey: false,
    shiftKey: opts.shift ?? false,
  });
}

function draft(): Wf {
  return useWorkflowEditStore.getState().draft as Wf;
}

function nodeKeys(): string[] {
  return Object.keys(draft()).filter((k) => /^node_\d+$/.test(k));
}

beforeEach(() => {
  useWorkflowEditStore.setState({ clipboard: [] });
  useUIStore.getState().setCanvasReadOnly(false);
  seedDraft();
  render(<KeyboardShortcuts />);
});

afterEach(() => {
  document.body.innerHTML = '';
});

describe('Delete / Backspace', () => {
  it('removes the selected node from the draft', () => {
    selectNode('node_2');
    press('Delete');
    expect(draft().node_2).toBeUndefined();
    expect(draft().node_1.children).toEqual([]);
  });

  it('Backspace also removes the selected node', () => {
    selectNode('node_2');
    press('Backspace');
    expect(draft().node_2).toBeUndefined();
  });

  it('disconnects the selected edge when no node is selected', () => {
    selectEdge('node_1', 'node_2');
    press('Delete');
    expect(draft().node_1.children).toEqual([]);
    // The node itself survives — only the edge was removed.
    expect(draft().node_2).toBeDefined();
  });

  it('is a no-op while typing in an input (edits text, not the graph)', () => {
    selectNode('node_2');
    const input = document.createElement('input');
    document.body.appendChild(input);
    press('Backspace', { target: input });
    expect(draft().node_2).toBeDefined();
  });

  it('is a no-op when the canvas is read-only', () => {
    useUIStore.getState().setCanvasReadOnly(true);
    selectNode('node_2');
    press('Delete');
    expect(draft().node_2).toBeDefined();
  });
});

describe('Cmd+C / Cmd+V', () => {
  it('copies then pastes a duplicate with a fresh id at an offset', () => {
    selectNode('node_2');
    press('c', { meta: true });
    expect(useWorkflowEditStore.getState().clipboard).toHaveLength(1);

    expect(nodeKeys()).toHaveLength(2);
    press('v', { meta: true });

    const keys = nodeKeys();
    expect(keys).toHaveLength(3);
    const newId = keys.find((k) => k !== 'node_1' && k !== 'node_2')!;
    const pasted = draft()[newId];
    // Fresh disconnected node, offset +24 from node_2's (300,400).
    expect(pasted.node_type).toBe('EndNode');
    expect(pasted.children).toEqual([]);
    expect(pasted.__attributes__).toMatchObject({ x: 324, y: 424 });
  });

  it('Cmd+V is a no-op when the clipboard is empty', () => {
    press('v', { meta: true });
    expect(nodeKeys()).toHaveLength(2);
  });

  it('Cmd+C is a no-op when read-only', () => {
    useUIStore.getState().setCanvasReadOnly(true);
    selectNode('node_2');
    press('c', { meta: true });
    expect(useWorkflowEditStore.getState().clipboard).toHaveLength(0);
  });

  it('Cmd+C does not fire while editing text', () => {
    selectNode('node_2');
    const input = document.createElement('input');
    document.body.appendChild(input);
    press('c', { meta: true, target: input });
    expect(useWorkflowEditStore.getState().clipboard).toHaveLength(0);
  });

  it('Cmd+C defers to native text copy when text is selected (no node copy)', () => {
    selectNode('node_2');
    // Simulate a real, non-collapsed text selection (e.g. dragged across sider
    // text). The handler must NOT hijack Cmd+C into a node copy.
    const sel = { isCollapsed: false, toString: () => 'selected sider text' };
    const spy = vi
      .spyOn(window, 'getSelection')
      .mockReturnValue(sel as unknown as Selection);
    try {
      press('c', { meta: true });
      expect(useWorkflowEditStore.getState().clipboard).toHaveLength(0);
    } finally {
      spy.mockRestore();
    }
  });
});

describe('Cmd+D — duplicate', () => {
  it('duplicates the selected node in one step', () => {
    selectNode('node_2');
    press('d', { meta: true });
    const keys = nodeKeys();
    expect(keys).toHaveLength(3);
    const newId = keys.find((k) => k !== 'node_1' && k !== 'node_2')!;
    expect(draft()[newId].node_type).toBe('EndNode');
    expect(draft()[newId].__attributes__).toMatchObject({ x: 324, y: 424 });
  });

  it('is a no-op when read-only', () => {
    useUIStore.getState().setCanvasReadOnly(true);
    selectNode('node_2');
    press('d', { meta: true });
    expect(nodeKeys()).toHaveLength(2);
  });
});
