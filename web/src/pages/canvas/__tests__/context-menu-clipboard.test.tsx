/**
 * Stream 4 — ContextMenuLayer copy / paste wiring.
 *
 * Mirrors `context-menu-delete.test.tsx`: override only `useNodes`/`useEdges`
 * (to seed a selection) while keeping the rest of `@xyflow/react` real so the
 * surrounding `ReactFlowProvider` (and `useReactFlow().screenToFlowPosition`,
 * which `onPaste` uses) stays genuine. The copy/paste menu items call the
 * edit-store clipboard actions directly.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { ReactFlowProvider } from '@xyflow/react';

const selection = {
  nodes: [] as { id: string; selected: boolean }[],
  edges: [] as { id: string; source: string; target: string; selected: boolean }[],
};

vi.mock('@xyflow/react', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@xyflow/react')>();
  return {
    ...actual,
    useNodes: () => selection.nodes,
    useEdges: () => selection.edges,
  };
});

import { ContextMenuLayer } from '@/pages/canvas/ContextMenuLayer';
import { useWorkflowEditStore } from '@/stores/workflow-edit';

function renderLayer() {
  return render(
    <ReactFlowProvider>
      <ContextMenuLayer>
        <div data-testid="pane" style={{ width: 200, height: 200 }} />
      </ContextMenuLayer>
    </ReactFlowProvider>,
  );
}

function openMenu() {
  fireEvent.contextMenu(screen.getByTestId('pane'));
}

beforeEach(() => {
  selection.nodes = [];
  selection.edges = [];
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

describe('ContextMenuLayer copy / paste', () => {
  it('Copy node loads the clipboard with the selected node', () => {
    selection.nodes = [{ id: 'node_2', selected: true }];
    renderLayer();
    openMenu();
    fireEvent.click(screen.getByText('Copy node'));
    expect(useWorkflowEditStore.getState().clipboard).toHaveLength(1);
    expect(useWorkflowEditStore.getState().clipboard[0].node_type).toBe('EndNode');
  });

  it('Paste node inserts a fresh duplicate from the clipboard', () => {
    useWorkflowEditStore.getState().copyNodes([]);
    // Seed a clipboard entry directly so Paste is enabled.
    useWorkflowEditStore.setState({
      clipboard: [{ node_id: 'node_2', node_type: 'EndNode', children: [] }],
    });
    renderLayer();
    openMenu();
    fireEvent.click(screen.getByText('Paste node'));
    const draft = useWorkflowEditStore.getState().draft as Record<string, any>;
    const keys = Object.keys(draft).filter((k) => /^node_\d+$/.test(k));
    expect(keys).toHaveLength(3);
  });

  it('Paste node is disabled when the clipboard is empty', () => {
    renderLayer();
    openMenu();
    const item = screen.getByText('Paste node').closest('[role="menuitem"]')!;
    // Radix marks a disabled ContextMenuItem with aria-disabled + data-disabled.
    expect(item.getAttribute('aria-disabled')).toBe('true');
  });
});
