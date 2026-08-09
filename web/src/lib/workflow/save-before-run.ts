/**
 * Save-if-dirty-then-run orchestration (shared by the workflow Execute and
 * Batch Execute paths).
 *
 * THE BUG THIS FIXES: both execution entry points POST only the `wf_id`
 * (`.../executions` and `.../batch`); the backend then loads the *committed*
 * version of that workflow. So when the user has unsaved canvas edits and hits
 * Execute, the engine runs the LAST SAVED version, not what's on screen.
 *
 * THE FIX: before starting execution, if the draft has unsaved changes, commit
 * it first (a new subversion). The `wf_id` is unchanged — the commit just
 * advances the committed HEAD — so the subsequent `wf_id`-keyed execution now
 * loads the just-saved current workflow.
 *
 * Sequencing contract:
 *   - DIRTY  → `await save(draft)`; only if it RESOLVES do we `await run()`.
 *   - CLEAN  → skip the save entirely (no redundant identical subversion) and
 *              `await run()` directly.
 *   - SAVE FAILS → `run()` is NEVER called; the rejection propagates so the
 *              caller's own save-error toast fires and execution is aborted.
 *
 * The caller owns BOTH the commit (a TanStack `mutateAsync`, which already
 * toasts on error + re-baselines `dirty` via `markSaved` on success) and the
 * execution call, so this helper stays a pure dependency-minimal orchestrator:
 * it never reads a store or fires a toast itself.
 */
export interface SaveBeforeRunArgs<T> {
  /** DERIVED unsaved-changes truth (`useWorkflowEditStore.isDirty()`). */
  dirty: boolean;
  /** The current draft to commit (only used when `dirty`). */
  draft: T;
  /**
   * Commit the draft as a new subversion. MUST reject on failure (a TanStack
   * `mutateAsync` does). When it rejects, `run` is not called and the
   * rejection propagates.
   */
  save: (draft: T) => Promise<unknown>;
  /** Start the execution. Only invoked once the workflow is guaranteed saved. */
  run: () => Promise<unknown>;
}

/**
 * Commit the draft first when dirty, then execute. See the module docstring
 * for the full contract. Rejects (without running) if the save rejects.
 */
export async function saveBeforeRun<T>({
  dirty,
  draft,
  save,
  run,
}: SaveBeforeRunArgs<T>): Promise<void> {
  if (dirty) {
    // If this rejects, the await throws and `run()` below is never reached.
    await save(draft);
  }
  await run();
}
