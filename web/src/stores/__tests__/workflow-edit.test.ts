/**
 * Unit tests for the workflow-edit zustand store.
 *
 * Targets the four invariants the rest of the UI depends on:
 *   1. `setDraft` seeds and resets dirty + history.
 *   2. `applyEdit` deep-clones (no aliasing with prior snapshot) and
 *      pushes to `undoStack` while clearing `redoStack`.
 *   3. `undo` round-trips through `JSON.stringify` and leaves `dirty: true`.
 *   4. `redo` reverses an `undo`; a fresh `applyEdit` drops the redo stack
 *      (classic linear-history rule).
 *
 * We reset the store between tests by calling `setDraft(null)` which the
 * store treats as "switch workflows" — undoStack/redoStack are cleared.
 */
import { beforeEach, describe, expect, it } from 'vitest';
import { useWorkflowEditStore } from '@/stores/workflow-edit';

describe('useWorkflowEditStore', () => {
  beforeEach(() => {
    useWorkflowEditStore.getState().setDraft(null);
  });

  it('setDraft seeds the draft and clears history + dirty flag', () => {
    const store = useWorkflowEditStore.getState();
    store.setDraft({ a: 1 });

    const s = useWorkflowEditStore.getState();
    expect(s.draft).toEqual({ a: 1 });
    expect(s.dirty).toBe(false);
    expect(s.undoStack).toEqual([]);
    expect(s.redoStack).toEqual([]);
  });

  it('applyEdit pushes prior snapshot, sets dirty, and clears redo stack', () => {
    const api = useWorkflowEditStore.getState();
    api.setDraft({ a: 1 });

    // Seed a redo entry to confirm applyEdit drops it.
    api.applyEdit((wf) => ({ ...wf, a: 2 }));
    api.undo();
    expect(useWorkflowEditStore.getState().redoStack.length).toBe(1);

    useWorkflowEditStore.getState().applyEdit((wf) => ({ ...wf, b: 9 }));

    const s = useWorkflowEditStore.getState();
    expect(s.draft).toEqual({ a: 1, b: 9 });
    expect(s.dirty).toBe(true);
    expect(s.undoStack.length).toBe(1);
    expect(s.redoStack).toEqual([]);
  });

  it('applyEdit does not alias the previous snapshot (structuredClone)', () => {
    const api = useWorkflowEditStore.getState();
    const seed = { nested: { count: 1 } };
    api.setDraft(seed);
    api.applyEdit((wf) => {
      const nested = wf.nested as { count: number };
      nested.count = 42;
      return wf;
    });

    // The seed object the test holds must NOT have been mutated — the
    // store's `structuredClone` should have detached the draft.
    expect(seed.nested.count).toBe(1);
    const draft = useWorkflowEditStore.getState().draft as { nested: { count: number } };
    expect(draft.nested.count).toBe(42);
  });

  it('undo restores the prior snapshot; dirty re-derives (back to baseline = clean)', () => {
    const api = useWorkflowEditStore.getState();
    api.setDraft({ a: 1 });
    api.applyEdit((wf) => ({ ...wf, a: 2 }));
    api.undo();

    const s = useWorkflowEditStore.getState();
    expect(s.draft).toEqual({ a: 1 });
    // Stream 0a: undo back to the baseline bytes derives clean (the old
    // hard `dirty:true` is removed).
    expect(s.dirty).toBe(false);
    expect(s.isDirty()).toBe(false);
    expect(s.undoStack).toEqual([]);
    expect(s.redoStack.length).toBe(1);
  });

  it('undo to a still-divergent snapshot stays dirty', () => {
    const api = useWorkflowEditStore.getState();
    api.setDraft({ a: 1 });
    api.applyEdit((wf) => ({ ...wf, a: 2 }));
    api.applyEdit((wf) => ({ ...wf, a: 3 }));
    api.undo(); // back to { a: 2 } — still != baseline { a: 1 }

    const s = useWorkflowEditStore.getState();
    expect(s.draft).toEqual({ a: 2 });
    expect(s.dirty).toBe(true);
    expect(s.isDirty()).toBe(true);
  });

  it('redo reverses an undo', () => {
    const api = useWorkflowEditStore.getState();
    api.setDraft({ a: 1 });
    api.applyEdit((wf) => ({ ...wf, a: 2 }));
    api.undo();
    api.redo();

    const s = useWorkflowEditStore.getState();
    expect(s.draft).toEqual({ a: 2 });
    expect(s.undoStack.length).toBe(1);
    expect(s.redoStack).toEqual([]);
  });

  it('markClean flips dirty back to false without touching the draft', () => {
    const api = useWorkflowEditStore.getState();
    api.setDraft({ a: 1 });
    api.applyEdit((wf) => ({ ...wf, a: 2 }));
    expect(useWorkflowEditStore.getState().dirty).toBe(true);

    useWorkflowEditStore.getState().markClean();
    const s = useWorkflowEditStore.getState();
    expect(s.dirty).toBe(false);
    expect(s.draft).toEqual({ a: 2 });
  });
});
