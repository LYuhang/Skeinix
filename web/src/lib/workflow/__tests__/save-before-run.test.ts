/**
 * Unit tests for `saveBeforeRun` — the save-if-dirty orchestration shared by
 * the workflow Execute (WorkflowRunTab) and Batch Execute (BatchTab) paths.
 *
 * The three behaviours that make Execute run the CURRENT canvas (not the
 * last-saved version) without redundant commits:
 *   1. dirty  → save is awaited BEFORE run.
 *   2. clean  → run only; save never called (no redundant identical subversion).
 *   3. save fails → run is NEVER called; the rejection propagates.
 */
import { describe, expect, it, vi } from 'vitest';
import { saveBeforeRun } from '@/lib/workflow/save-before-run';

describe('saveBeforeRun', () => {
  it('dirty: saves first, THEN runs (save resolves before run starts)', async () => {
    const order: string[] = [];
    const save = vi.fn(async () => {
      order.push('save');
    });
    const run = vi.fn(async () => {
      order.push('run');
    });

    await saveBeforeRun({ dirty: true, draft: { a: 1 }, save, run });

    expect(save).toHaveBeenCalledTimes(1);
    expect(save).toHaveBeenCalledWith({ a: 1 });
    expect(run).toHaveBeenCalledTimes(1);
    // Save strictly precedes run.
    expect(order).toEqual(['save', 'run']);
  });

  it('clean: runs only, save is NOT called (no redundant subversion)', async () => {
    const save = vi.fn(async () => {});
    const run = vi.fn(async () => {});

    await saveBeforeRun({ dirty: false, draft: { a: 1 }, save, run });

    expect(save).not.toHaveBeenCalled();
    expect(run).toHaveBeenCalledTimes(1);
  });

  it('save fails: run is NEVER called and the rejection propagates', async () => {
    const boom = new Error('Save failed: 500');
    const save = vi.fn(async () => {
      throw boom;
    });
    const run = vi.fn(async () => {});

    await expect(
      saveBeforeRun({ dirty: true, draft: { a: 1 }, save, run }),
    ).rejects.toBe(boom);

    expect(save).toHaveBeenCalledTimes(1);
    expect(run).not.toHaveBeenCalled();
  });

  it('does not start the run until the save promise settles', async () => {
    let resolveSave!: () => void;
    const savePromise = new Promise<void>((r) => {
      resolveSave = r;
    });
    const save = vi.fn(() => savePromise);
    const run = vi.fn(async () => {});

    const p = saveBeforeRun({ dirty: true, draft: {}, save, run });
    // Let microtasks flush — run must NOT have fired while save is pending.
    await Promise.resolve();
    expect(run).not.toHaveBeenCalled();

    resolveSave();
    await p;
    expect(run).toHaveBeenCalledTimes(1);
  });
});
