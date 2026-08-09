/**
 * Stream 0c / Stream 1 — graph-mutation store actions.
 *
 * Each batched action must be exactly ONE undo step and must maintain the
 * `children ↔ next_node_id` membership invariant (incl the mandatory
 * Condition "others" row with `condition_str:"others"`). Also covers the
 * cycle guard, batched addNodes id-collision-free, two-way pairNodes, the
 * clipboard reset-on-paste, and the derived save-state model.
 */
import { beforeEach, describe, expect, it } from 'vitest';
import {
  useWorkflowEditStore,
  syncTypeConfigOnEdgeChange,
  wouldCreateCycle,
  type WorkflowDraft,
} from '@/stores/workflow-edit';

type Rec = Record<string, any>;

function reset() {
  useWorkflowEditStore.getState().setDraft(null);
  // setDraft does not clear the in-memory clipboard slice; reset it so
  // clipboard tests don't leak across cases.
  useWorkflowEditStore.setState({ clipboard: [] });
}

function api() {
  return useWorkflowEditStore.getState();
}

function draft(): Rec {
  return useWorkflowEditStore.getState().draft as Rec;
}

/** undoStack length BEFORE an action; assert it grew by exactly 1 after. */
function expectOneUndoStep(before: number) {
  expect(useWorkflowEditStore.getState().undoStack.length).toBe(before + 1);
}

beforeEach(reset);

describe('connectNodes — plain source', () => {
  it('pushes target into children (dedup) in one undo step', () => {
    api().setDraft({
      node_1: { node_id: 'node_1', node_type: 'StartNode', children: [] },
      node_2: { node_id: 'node_2', node_type: 'EndNode', children: [] },
    });
    const before = api().undoStack.length;
    api().connectNodes('node_1', 'node_2');
    expectOneUndoStep(before);
    expect(draft().node_1.children).toEqual(['node_2']);
    // idempotent / dedup
    api().connectNodes('node_1', 'node_2');
    expect(draft().node_1.children).toEqual(['node_2']);
  });
});

describe('connectNodes — ConditionNode source (type-aware)', () => {
  it('FIRST connect makes "others" the default target (no new branch card)', () => {
    api().setDraft({
      node_1: {
        node_id: 'node_1',
        node_type: 'ConditionNode',
        children: [],
        node_config: { conditions: [] },
      },
      node_2: { node_id: 'node_2', node_type: 'CodeNode', children: [] },
    });
    api().connectNodes('node_1', 'node_2');
    const conds = draft().node_1.node_config.conditions as Rec[];
    // [others→node_2]: exactly one card, the others card.
    expect(conds.length).toBe(1);
    const others = conds.find((c) => c.condition_str === 'others');
    expect(others).toBeTruthy();
    expect(others!.condition_name).toBe('others');
    expect(others!.next_node_id).toBe('node_2');
    expect(draft().node_1.children).toEqual(['node_2']);
  });

  it('SECOND connect appends a branch card BEFORE others (others stays last)', () => {
    api().setDraft({
      node_1: {
        node_id: 'node_1',
        node_type: 'ConditionNode',
        children: [],
        node_config: { conditions: [] },
      },
      node_2: { node_id: 'node_2', node_type: 'CodeNode', children: [] },
      node_3: { node_id: 'node_3', node_type: 'CodeNode', children: [] },
    });
    api().connectNodes('node_1', 'node_2'); // [others→node_2]
    api().connectNodes('node_1', 'node_3'); // [branch→node_3, others→node_2]
    const conds = draft().node_1.node_config.conditions as Rec[];
    expect(conds.length).toBe(2);
    // others LAST.
    expect(conds[conds.length - 1].condition_str).toBe('others');
    expect(conds[conds.length - 1].next_node_id).toBe('node_2');
    // new branch card targets node_3, empty str, inserted before others.
    const branch = conds.find((c) => c.next_node_id === 'node_3')!;
    expect(branch.condition_str).toBe('');
    expect(conds.indexOf(branch)).toBeLessThan(conds.length - 1);
    expect(draft().node_1.children).toEqual(['node_2', 'node_3']);
  });

  it('connect is idempotent for an already-mapped target', () => {
    api().setDraft({
      node_1: {
        node_id: 'node_1',
        node_type: 'ConditionNode',
        children: [],
        node_config: { conditions: [] },
      },
      node_2: { node_id: 'node_2', node_type: 'CodeNode', children: [] },
    });
    api().connectNodes('node_1', 'node_2');
    api().connectNodes('node_1', 'node_2');
    const conds = draft().node_1.node_config.conditions as Rec[];
    expect(conds.length).toBe(1);
    expect(conds[0].next_node_id).toBe('node_2');
  });
});

describe('connectNodes — ParallelStartNode source', () => {
  it('appends a branch entry (dedup by next_node_id)', () => {
    api().setDraft({
      node_1: {
        node_id: 'node_1',
        node_type: 'ParallelStartNode',
        children: [],
        node_config: { branches: {}, parallel_end_node_id: null },
      },
      node_2: { node_id: 'node_2', node_type: 'CodeNode', children: [] },
    });
    api().connectNodes('node_1', 'node_2');
    const branches = draft().node_1.node_config.branches as Rec;
    const vals = Object.values(branches);
    expect(vals.length).toBe(1);
    expect((vals[0] as Rec).next_node_id).toBe('node_2');
    expect(draft().node_1.children).toEqual(['node_2']);
  });
});

describe('connectNodes — LoopBeginNode rejects a 2nd child', () => {
  it('leaves the draft unchanged when a 2nd outgoing child is attempted', () => {
    api().setDraft({
      node_1: {
        node_id: 'node_1',
        node_type: 'LoopBeginNode',
        children: ['node_2'],
        node_config: {},
      },
      node_2: { node_id: 'node_2', node_type: 'CodeNode', children: [] },
      node_3: { node_id: 'node_3', node_type: 'CodeNode', children: [] },
    });
    api().connectNodes('node_1', 'node_3');
    expect(draft().node_1.children).toEqual(['node_2']);
  });

  it('allows the first child', () => {
    api().setDraft({
      node_1: { node_id: 'node_1', node_type: 'LoopBeginNode', children: [], node_config: {} },
      node_2: { node_id: 'node_2', node_type: 'CodeNode', children: [] },
    });
    api().connectNodes('node_1', 'node_2');
    expect(draft().node_1.children).toEqual(['node_2']);
  });
});

describe('disconnectNodes', () => {
  it('removes target from children + DELETES the matching non-others Condition card', () => {
    api().setDraft({
      node_1: {
        node_id: 'node_1',
        node_type: 'ConditionNode',
        children: ['node_2', 'node_3'],
        node_config: {
          conditions: [
            { condition_name: 'high', condition_str: '{x} > 1', next_node_id: 'node_2' },
            { condition_name: 'others', condition_str: 'others', next_node_id: 'node_3' },
          ],
        },
      },
      node_2: { node_id: 'node_2', node_type: 'CodeNode', children: [] },
      node_3: { node_id: 'node_3', node_type: 'CodeNode', children: [] },
    });
    const before = api().undoStack.length;
    api().disconnectNodes('node_1', 'node_2'); // delete the "high" branch card
    expectOneUndoStep(before);
    expect(draft().node_1.children).toEqual(['node_3']);
    const conds = draft().node_1.node_config.conditions as Rec[];
    // the "high" card is GONE; others (→node_3) remains, last.
    expect(conds.find((c) => c.condition_name === 'high')).toBeUndefined();
    expect(conds.length).toBe(1);
    expect(conds[0].condition_str).toBe('others');
    expect(conds[0].next_node_id).toBe('node_3');
  });

  it("disconnecting others' target NULLs others (keeps the card)", () => {
    api().setDraft({
      node_1: {
        node_id: 'node_1',
        node_type: 'ConditionNode',
        children: ['node_2'],
        node_config: {
          conditions: [
            { condition_name: 'others', condition_str: 'others', next_node_id: 'node_2' },
          ],
        },
      },
      node_2: { node_id: 'node_2', node_type: 'CodeNode', children: [] },
    });
    api().disconnectNodes('node_1', 'node_2');
    expect(draft().node_1.children).toEqual([]);
    const conds = draft().node_1.node_config.conditions as Rec[];
    expect(conds.length).toBe(1);
    expect(conds[0].condition_str).toBe('others');
    expect(conds[0].next_node_id).toBeNull();
  });

  it('DROPS the matching Parallel branch (by next_node_id, not name)', () => {
    api().setDraft({
      node_1: {
        node_id: 'node_1',
        node_type: 'ParallelStartNode',
        children: ['node_2'],
        node_config: {
          branches: { branch_1: { branch_description: '', next_node_id: 'node_2' } },
          parallel_end_node_id: null,
        },
      },
      node_2: { node_id: 'node_2', node_type: 'CodeNode', children: [] },
    });
    api().disconnectNodes('node_1', 'node_2');
    expect(draft().node_1.children).toEqual([]);
    expect(Object.keys(draft().node_1.node_config.branches)).toEqual([]);
  });
});

describe('removeNode / removeNodes', () => {
  it('deletes node(s), strips children, NULLs condition targets, drops branches, clears pairing — one step', () => {
    api().setDraft({
      node_1: {
        node_id: 'node_1',
        node_type: 'ConditionNode',
        children: ['node_3'],
        node_config: {
          conditions: [
            { condition_name: 'a', condition_str: '{x}>1', next_node_id: 'node_3' },
            { condition_name: 'others', condition_str: 'others', next_node_id: null },
          ],
        },
      },
      node_2: {
        node_id: 'node_2',
        node_type: 'ParallelEndNode',
        children: ['node_3'],
        node_config: { parallel_start_node_id: 'node_3' },
      },
      node_3: {
        node_id: 'node_3',
        node_type: 'ParallelStartNode',
        children: [],
        node_config: {
          branches: {},
          parallel_end_node_id: 'node_2',
          loop_begin_node_id: 'node_3',
        },
      },
    });
    const before = api().undoStack.length;
    api().removeNode('node_3');
    expectOneUndoStep(before);
    const d = draft();
    expect(d.node_3).toBeUndefined();
    expect(d.node_1.children).toEqual([]);
    // deleting node_3 DELETES the non-others "a" card; others remains + last.
    const conds = d.node_1.node_config.conditions as Rec[];
    expect(conds.find((c) => c.condition_name === 'a')).toBeUndefined();
    expect(conds[conds.length - 1].condition_str).toBe('others');
    expect(d.node_2.children).toEqual([]);
    expect(d.node_2.node_config.parallel_start_node_id).toBeNull();
  });

  it('deleting the node that "others" points at NULLs others (keeps the card)', () => {
    api().setDraft({
      node_1: {
        node_id: 'node_1',
        node_type: 'ConditionNode',
        children: ['node_2'],
        node_config: {
          conditions: [
            { condition_name: 'others', condition_str: 'others', next_node_id: 'node_2' },
          ],
        },
      },
      node_2: { node_id: 'node_2', node_type: 'CodeNode', children: [] },
    });
    api().removeNode('node_2');
    const d = draft();
    expect(d.node_2).toBeUndefined();
    expect(d.node_1.children).toEqual([]);
    const conds = d.node_1.node_config.conditions as Rec[];
    expect(conds.length).toBe(1);
    expect(conds[0].condition_str).toBe('others');
    expect(conds[0].next_node_id).toBeNull();
  });

  it('removeNodes does ALL deletions in ONE undo step', () => {
    api().setDraft({
      node_1: { node_id: 'node_1', node_type: 'StartNode', children: ['node_2', 'node_3'] },
      node_2: { node_id: 'node_2', node_type: 'CodeNode', children: [] },
      node_3: { node_id: 'node_3', node_type: 'CodeNode', children: [] },
    });
    const before = api().undoStack.length;
    api().removeNodes(['node_2', 'node_3']);
    expectOneUndoStep(before);
    const d = draft();
    expect(d.node_2).toBeUndefined();
    expect(d.node_3).toBeUndefined();
    expect(d.node_1.children).toEqual([]);
  });

  it('leaves dangling input_fields[*].reference untouched', () => {
    api().setDraft({
      node_1: { node_id: 'node_1', node_type: 'StartNode', children: ['node_2'] },
      node_2: {
        node_id: 'node_2',
        node_type: 'CodeNode',
        children: [],
        input_fields: { v: { type: 'number', value: 0, reference: 'gone.out' } },
      },
    });
    api().removeNodes(['node_1']);
    expect(draft().node_2.input_fields.v.reference).toBe('gone.out');
  });
});

describe('wouldCreateCycle — allows diamond, rejects cycle', () => {
  const diamond: WorkflowDraft = {
    node_1: { node_id: 'node_1', children: ['node_2', 'node_3'] },
    node_2: { node_id: 'node_2', children: ['node_4'] },
    node_3: { node_id: 'node_3', children: ['node_4'] },
    node_4: { node_id: 'node_4', children: [] },
  };

  it('allows a fan-in (node_2->node_4 already; node_3->node_4 is a diamond, not a cycle)', () => {
    expect(wouldCreateCycle(diamond, 'node_3', 'node_4')).toBe(false);
  });

  it('rejects a self-loop', () => {
    expect(wouldCreateCycle(diamond, 'node_2', 'node_2')).toBe(true);
  });

  it('rejects a back-edge that closes a cycle (node_4 -> node_1)', () => {
    expect(wouldCreateCycle(diamond, 'node_4', 'node_1')).toBe(true);
  });
});

describe('addNodes — batched, id-collision-free, one step', () => {
  it('allocates distinct fresh ids advancing within the batch', () => {
    api().setDraft({ node_2: { node_id: 'node_2' } });
    const before = api().undoStack.length;
    api().addNodes(
      [{ node_type: 'CodeNode' }, { node_type: 'PromptNode' }],
      [
        { x: 1, y: 1 },
        { x: 2, y: 2 },
      ],
    );
    expectOneUndoStep(before);
    const d = draft();
    expect(d.node_3).toMatchObject({ node_id: 'node_3', __attributes__: { x: 1, y: 1 } });
    expect(d.node_4).toMatchObject({ node_id: 'node_4', __attributes__: { x: 2, y: 2 } });
  });
});

describe('pairNodes — two-way + clear-old, one step', () => {
  it('sets both sides of a parallel pairing', () => {
    api().setDraft({
      node_1: { node_id: 'node_1', node_type: 'ParallelStartNode', children: [], node_config: {} },
      node_2: { node_id: 'node_2', node_type: 'ParallelEndNode', children: [], node_config: {} },
    });
    const before = api().undoStack.length;
    api().pairNodes('node_1', 'node_2', 'parallel');
    expectOneUndoStep(before);
    expect(draft().node_1.node_config.parallel_end_node_id).toBe('node_2');
    expect(draft().node_2.node_config.parallel_start_node_id).toBe('node_1');
  });

  it('clears the OLD partner back-pointer on re-point', () => {
    api().setDraft({
      node_1: {
        node_id: 'node_1',
        node_type: 'ParallelStartNode',
        children: [],
        node_config: { parallel_end_node_id: 'node_2' },
      },
      node_2: {
        node_id: 'node_2',
        node_type: 'ParallelEndNode',
        children: [],
        node_config: { parallel_start_node_id: 'node_1' },
      },
      node_3: {
        node_id: 'node_3',
        node_type: 'ParallelEndNode',
        children: [],
        node_config: { parallel_start_node_id: null },
      },
    });
    api().pairNodes('node_1', 'node_3', 'parallel');
    expect(draft().node_1.node_config.parallel_end_node_id).toBe('node_3');
    expect(draft().node_3.node_config.parallel_start_node_id).toBe('node_1');
    // old partner orphan cleared
    expect(draft().node_2.node_config.parallel_start_node_id).toBeNull();
  });

  it('works symmetrically from the End side for a loop pairing', () => {
    api().setDraft({
      node_1: { node_id: 'node_1', node_type: 'LoopBeginNode', children: [], node_config: {} },
      node_2: { node_id: 'node_2', node_type: 'LoopEndNode', children: [], node_config: {} },
    });
    api().pairNodes('node_2', 'node_1', 'loop'); // pass End first
    expect(draft().node_1.node_config.loop_end_node_id).toBe('node_2');
    expect(draft().node_2.node_config.loop_begin_node_id).toBe('node_1');
  });
});

describe('clipboard — copy/paste resets topology', () => {
  it('paste resets children + all next_node_id + pairing pointers to null/empty', () => {
    api().setDraft({
      node_1: {
        node_id: 'node_1',
        node_type: 'ConditionNode',
        children: ['node_2'],
        node_config: {
          conditions: [
            { condition_name: 'a', condition_str: '{x}>1', next_node_id: 'node_2' },
            { condition_name: 'others', condition_str: 'others', next_node_id: 'node_3' },
          ],
          parallel_end_node_id: 'node_9',
        },
      },
    });
    api().copyNodes(['node_1']);
    const before = api().undoStack.length;
    api().pasteNodes({ x: 10, y: 20 });
    expectOneUndoStep(before);
    const d = draft();
    const pasted = d.node_2 as Rec;
    expect(pasted).toBeTruthy();
    expect(pasted.children).toEqual([]);
    const conds = pasted.node_config.conditions as Rec[];
    expect(conds.every((c) => c.next_node_id === null)).toBe(true);
    expect(pasted.node_config.parallel_end_node_id).toBeNull();
    expect(pasted.__attributes__).toMatchObject({ x: 10, y: 20 });
  });

  it('paste no-ops on an empty clipboard', () => {
    api().setDraft({ node_1: { node_id: 'node_1' } });
    const before = api().undoStack.length;
    api().pasteNodes({ x: 0, y: 0 });
    expect(api().undoStack.length).toBe(before);
  });
});

describe('derived save-state model (Stream 0a)', () => {
  it('edit → dirty; undo back to baseline → clean; markSaved → clean', () => {
    api().setDraft({ a: 1 });
    expect(api().isDirty()).toBe(false);

    api().applyEdit((wf) => ({ ...wf, a: 2 }));
    expect(useWorkflowEditStore.getState().isDirty()).toBe(true);
    expect(useWorkflowEditStore.getState().dirty).toBe(true);

    api().undo();
    expect(useWorkflowEditStore.getState().isDirty()).toBe(false);
    expect(useWorkflowEditStore.getState().dirty).toBe(false);

    api().redo();
    expect(useWorkflowEditStore.getState().isDirty()).toBe(true);

    api().markSaved();
    expect(useWorkflowEditStore.getState().isDirty()).toBe(false);
    expect(useWorkflowEditStore.getState().dirty).toBe(false);
    // markSaved does NOT clear the undo stack.
    expect(useWorkflowEditStore.getState().undoStack.length).toBeGreaterThan(0);
  });

  it('undo PAST a save re-dirties (code-editor behaviour)', () => {
    api().setDraft({ a: 1 });
    api().applyEdit((wf) => ({ ...wf, a: 2 }));
    api().markSaved(); // baseline now { a: 2 }
    api().undo(); // back to { a: 1 } != baseline
    expect(useWorkflowEditStore.getState().isDirty()).toBe(true);
  });
});

describe('syncTypeConfigOnEdgeChange — direct contract', () => {
  it('returns false for a LoopBegin gaining a 2nd child', () => {
    const wf: WorkflowDraft = {
      node_1: { node_id: 'node_1', node_type: 'LoopBeginNode', children: ['node_2'], node_config: {} },
    };
    expect(syncTypeConfigOnEdgeChange(wf, 'node_1', 'node_3', 'add')).toBe(false);
  });

  it('returns true and is a no-op for a plain source', () => {
    const wf: WorkflowDraft = {
      node_1: { node_id: 'node_1', node_type: 'CodeNode', children: [], node_config: {} },
    };
    expect(syncTypeConfigOnEdgeChange(wf, 'node_1', 'node_2', 'add')).toBe(true);
    expect((wf.node_1 as Rec).node_config).toEqual({});
  });
});
