/**
 * Bug A — server-meta merge + graph-vs-meta projection.
 *
 * `applyServerMeta(meta)` is the seam the CanvasPage seed effect uses on a
 * benign meta-only refetch (e.g. the user's own rename): it must apply the
 * new `__meta__` to BOTH the draft and the baseline WITHOUT clobbering the
 * user's unsaved GRAPH edits, and keep `dirty` deriving off the GRAPH delta
 * only. `stripWorkflowMeta` is the pure projection that drops `__meta__` so
 * the effect can compare the server graph to the baseline graph.
 */
import { beforeEach, describe, expect, it } from 'vitest';
import { useWorkflowEditStore, stripWorkflowMeta } from '@/stores/workflow-edit';

describe('stripWorkflowMeta', () => {
  it('drops __meta__ but keeps node_* entries (incl. their __attributes__)', () => {
    const wf = {
      __meta__: { workflow_name: 'Old' },
      node_1: { node_id: 'node_1', __attributes__: { x: 10, y: 20 } },
    };
    expect(stripWorkflowMeta(wf)).toEqual({
      node_1: { node_id: 'node_1', __attributes__: { x: 10, y: 20 } },
    });
  });

  it('two workflows differing ONLY in __meta__ project equal', () => {
    const graph = { node_1: { node_id: 'node_1' } };
    const a = { __meta__: { workflow_name: 'A' }, ...graph };
    const b = { __meta__: { workflow_name: 'B' }, ...graph };
    expect(JSON.stringify(stripWorkflowMeta(a))).toBe(
      JSON.stringify(stripWorkflowMeta(b)),
    );
  });

  it('a graph edit survives the projection (registers as different)', () => {
    const a = { __meta__: { workflow_name: 'X' }, node_1: { children: [] } };
    const b = { __meta__: { workflow_name: 'X' }, node_1: { children: ['node_2'] } };
    expect(JSON.stringify(stripWorkflowMeta(a))).not.toBe(
      JSON.stringify(stripWorkflowMeta(b)),
    );
  });
});

describe('applyServerMeta', () => {
  beforeEach(() => {
    useWorkflowEditStore.getState().setDraft(null);
  });

  it('merges new __meta__ into draft + baseline WITHOUT clobbering unsaved graph edits', () => {
    const api = useWorkflowEditStore.getState();
    // Seed a clean draft, then make an unsaved GRAPH edit.
    api.setDraft({ __meta__: { workflow_name: 'Old' }, node_1: { children: [] } });
    api.applyEdit((wf) => {
      (wf.node_1 as Record<string, unknown>).children = ['node_2'];
      return wf;
    });
    expect(useWorkflowEditStore.getState().dirty).toBe(true);

    // A rename refetch arrives carrying new __meta__.
    useWorkflowEditStore.getState().applyServerMeta({ workflow_name: 'New' });

    const s = useWorkflowEditStore.getState();
    // New name applied to the draft...
    expect((s.draft as Record<string, unknown>).__meta__).toEqual({
      workflow_name: 'New',
    });
    // ...the user's unsaved graph edit is preserved...
    expect((s.draft as Record<string, { children: string[] }>).node_1.children).toEqual(
      ['node_2'],
    );
    // ...and dirty STAYS true (the unsaved graph edit still diverges from the
    // rebased baseline — only the meta change was folded into the baseline).
    expect(s.dirty).toBe(true);
  });

  it('on a CLEAN draft the rename stays clean (meta rebased into baseline)', () => {
    const api = useWorkflowEditStore.getState();
    api.setDraft({ __meta__: { workflow_name: 'Old' }, node_1: { children: [] } });
    expect(useWorkflowEditStore.getState().dirty).toBe(false);

    useWorkflowEditStore.getState().applyServerMeta({ workflow_name: 'New' });

    const s = useWorkflowEditStore.getState();
    expect((s.draft as Record<string, unknown>).__meta__).toEqual({
      workflow_name: 'New',
    });
    // Both draft and baseline carry the new meta → still clean.
    expect(s.dirty).toBe(false);
    expect(s.isDirty()).toBe(false);
  });

  it('produces a new draft reference (so xyflow / selectors re-render)', () => {
    const api = useWorkflowEditStore.getState();
    const seed = { __meta__: { workflow_name: 'Old' }, node_1: {} };
    api.setDraft(seed);
    const before = useWorkflowEditStore.getState().draft;
    useWorkflowEditStore.getState().applyServerMeta({ workflow_name: 'New' });
    expect(useWorkflowEditStore.getState().draft).not.toBe(before);
  });

  it('is a no-op while the draft is null', () => {
    useWorkflowEditStore.getState().applyServerMeta({ workflow_name: 'New' });
    expect(useWorkflowEditStore.getState().draft).toBeNull();
  });
});
