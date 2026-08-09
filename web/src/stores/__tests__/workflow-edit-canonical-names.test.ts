/**
 * addNode applies the engine's strict naming/config defaults so a hand-builder
 * never trips a jsonschema Check error (see node-type-defaults.ts).
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { useWorkflowEditStore } from '@/stores/workflow-edit';

beforeEach(() => {
  useWorkflowEditStore.setState({
    draft: null,
    dirty: false,
    baseline: 'null',
    undoStack: [],
    redoStack: [],
  });
});

describe('addNode — canonical names + CodeNode skeleton', () => {
  it('names an added StartNode __start__', () => {
    const s = useWorkflowEditStore.getState();
    s.setDraft({});
    s.addNode({ node_type: 'StartNode' }, { x: 0, y: 0 });
    const draft = useWorkflowEditStore.getState().draft as Record<string, any>;
    expect(draft.node_1.node_name).toBe('__start__');
  });

  it('names an added EndNode __end__', () => {
    const s = useWorkflowEditStore.getState();
    s.setDraft({});
    s.addNode({ node_type: 'EndNode' }, { x: 0, y: 0 });
    const draft = useWorkflowEditStore.getState().draft as Record<string, any>;
    expect(draft.node_1.node_name).toBe('__end__');
  });

  it('seeds a runnable process_fn skeleton on an added CodeNode', () => {
    const s = useWorkflowEditStore.getState();
    s.setDraft({});
    s.addNode({ node_type: 'CodeNode' }, { x: 0, y: 0 });
    const draft = useWorkflowEditStore.getState().draft as Record<string, any>;
    expect(draft.node_1.node_config.programming_language).toBe('python');
    expect(draft.node_1.node_config.process_fn).toContain('def process_fn(inputs):');
  });

  it('keeps a free-named node (PromptNode) defaulting to its id', () => {
    const s = useWorkflowEditStore.getState();
    s.setDraft({});
    s.addNode({ node_type: 'PromptNode' }, { x: 0, y: 0 });
    const draft = useWorkflowEditStore.getState().draft as Record<string, any>;
    expect(draft.node_1.node_name).toBe('node_1');
  });
});
