/**
 * Stream 8 (M1) — onboarding StartNode seed.
 *
 * The CanvasPage mount effect calls `shouldSeedStartNode(draft, readOnly)` to
 * decide whether to seed, then `addNode(START_NODE_PAYLOAD, …)` to do it. We
 * test the pure guard + the real store integration together (rather than
 * rendering the heavy route): the guard owns "when", the store owns "what".
 */
import { beforeEach, describe, expect, it } from 'vitest';
import {
  shouldSeedStartNode,
  START_NODE_PAYLOAD,
  hasStartNode,
  getStartNodeFields,
} from '@/lib/workflow/start-node';
import { useWorkflowEditStore } from '@/stores/workflow-edit';

beforeEach(() => {
  // Reset the store between tests.
  useWorkflowEditStore.setState({
    draft: null,
    dirty: false,
    baseline: 'null',
    undoStack: [],
    redoStack: [],
    clipboard: [],
  });
});

describe('shouldSeedStartNode (M1 guard)', () => {
  it('seeds an empty (brand-new) workflow', () => {
    expect(shouldSeedStartNode({}, false)).toBe(true);
  });

  it('does NOT seed when a StartNode already exists', () => {
    const wf = { node_1: { node_id: 'node_1', node_type: 'StartNode' } };
    expect(shouldSeedStartNode(wf, false)).toBe(false);
  });

  it('does NOT seed in read-only (pinned-version) mode, even when empty', () => {
    expect(shouldSeedStartNode({}, true)).toBe(false);
  });

  it('does NOT seed before the server snapshot arrives (null draft)', () => {
    expect(shouldSeedStartNode(null, false)).toBe(false);
  });

  it('ignores the reserved __meta__ key when deciding emptiness', () => {
    const wf = { __meta__: { name: 'x' } };
    expect(shouldSeedStartNode(wf, false)).toBe(true);
  });

  it('seeds when only non-Start nodes exist (no StartNode present)', () => {
    const wf = { node_1: { node_id: 'node_1', node_type: 'CodeNode' } };
    expect(shouldSeedStartNode(wf, false)).toBe(true);
  });
});

describe('seed via the store (M1 effect — addNode on an empty draft)', () => {
  it('adds exactly one editable StartNode to an empty draft', () => {
    const store = useWorkflowEditStore.getState();
    store.setDraft({});
    expect(hasStartNode(useWorkflowEditStore.getState().draft)).toBe(false);

    // Mirror the effect: guard true → addNode(skeleton).
    useWorkflowEditStore.getState().addNode(START_NODE_PAYLOAD, { x: 0, y: 0 });

    const draft = useWorkflowEditStore.getState().draft!;
    expect(hasStartNode(draft)).toBe(true);
    const node = draft.node_1 as Record<string, unknown>;
    expect(node.node_type).toBe('StartNode');
    expect(node.node_id).toBe('node_1');
    // applyNodeTypeDefaults stamps the reserved name for a StartNode (800b993).
    expect(node.node_name).toBe('__start__');
    expect(node.__attributes__).toEqual({ x: 0, y: 0 });
    // A freshly-seeded StartNode declares no input fields yet.
    expect(getStartNodeFields(draft)).toEqual([]);
    // One undo step (the seed is a normal, recoverable edit).
    expect(useWorkflowEditStore.getState().undoStack).toHaveLength(1);
  });

  it('does not double-seed: a draft already carrying a StartNode is left alone', () => {
    const existing = {
      node_1: { node_id: 'node_1', node_type: 'StartNode', children: [] },
    };
    useWorkflowEditStore.getState().setDraft(existing);
    // The effect would short-circuit on the guard; assert it stays single.
    expect(shouldSeedStartNode(useWorkflowEditStore.getState().draft, false)).toBe(false);
    const keys = Object.keys(useWorkflowEditStore.getState().draft!).filter((k) => !k.startsWith('__'));
    expect(keys).toEqual(['node_1']);
  });
});
