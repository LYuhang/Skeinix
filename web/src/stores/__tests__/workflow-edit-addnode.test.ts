import { describe, it, expect, beforeEach } from 'vitest';
import { useWorkflowEditStore } from '@/stores/workflow-edit';

beforeEach(() => {
  useWorkflowEditStore.setState({ draft: null, dirty: false, undoStack: [], redoStack: [] });
});

describe('addNode', () => {
  it('allocates node_1 on an empty draft, sets node_id/node_name/position, marks dirty', () => {
    const s = useWorkflowEditStore.getState();
    s.setDraft({});
    s.addNode({ node_type: 'PromptNode' }, { x: 5, y: 7 });
    const draft = useWorkflowEditStore.getState().draft as Record<string, any>;
    expect(draft.node_1).toMatchObject({
      node_id: 'node_1',
      node_type: 'PromptNode',
      node_name: 'node_1',
      __attributes__: { x: 5, y: 7 },
    });
    expect(useWorkflowEditStore.getState().dirty).toBe(true);
  });

  it('allocates max+1 and KEEPS a payload-provided node_name (template case)', () => {
    const s = useWorkflowEditStore.getState();
    s.setDraft({ node_3: { node_id: 'node_3' } });
    s.addNode({ node_type: 'CodeNode', node_name: 'Dedup rows' }, { x: 0, y: 0 });
    const draft = useWorkflowEditStore.getState().draft as Record<string, any>;
    expect(draft.node_4).toMatchObject({ node_id: 'node_4', node_name: 'Dedup rows' });
  });

  it('defaults an EMPTY-string node_name to the id (falsy guard, not just undefined)', () => {
    const s = useWorkflowEditStore.getState();
    s.setDraft({});
    s.addNode({ node_type: 'PromptNode', node_name: '' }, { x: 0, y: 0 });
    const draft = useWorkflowEditStore.getState().draft as Record<string, any>;
    expect(draft.node_1.node_name).toBe('node_1');
  });

  it('is undoable (one applyEdit snapshot)', () => {
    const s = useWorkflowEditStore.getState();
    s.setDraft({});
    s.addNode({ node_type: 'EndNode' }, { x: 1, y: 1 });
    useWorkflowEditStore.getState().undo();
    expect(useWorkflowEditStore.getState().draft).toEqual({});
  });

  it('merges into existing __attributes__ without dropping siblings', () => {
    const s = useWorkflowEditStore.getState();
    s.setDraft({});
    s.addNode({ node_type: 'CodeNode', __attributes__: { color: 'x' } }, { x: 9, y: 9 });
    const draft = useWorkflowEditStore.getState().draft as Record<string, any>;
    expect(draft.node_1.__attributes__).toEqual({ color: 'x', x: 9, y: 9 });
  });
});
