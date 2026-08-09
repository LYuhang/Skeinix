/**
 * Stream 0e — agent ↔ manual-edit reconciliation policy (pure).
 *
 * `CanvasPage`'s seed effect decides, on each fresh server snapshot, how to
 * reconcile it with the in-flight draft. The branching is extracted here so
 * it can be unit-tested without rendering the whole route (which pulls in
 * query hooks + react-router). The effect supplies the live facts; this
 * returns a discriminated decision the effect then carries out (calling
 * `setDraft` / showing the actionable toast).
 *
 * Decisions:
 *   - `seed`       → re-seed the draft from the server (first load, route /
 *                    version navigation, clean same-route refetch, or a server
 *                    echo of the user's OWN just-saved commit).
 *   - `meta-merge` → dirty draft, but the server's COMMITTED GRAPH still
 *                    equals the baseline graph (no agent commit) — the
 *                    refetch only carries new `__meta__` (e.g. the user's own
 *                    rename). Keep the draft graph + the user's unsaved edits,
 *                    merge the new `__meta__` into draft AND baseline, NO toast.
 *   - `conflict`   → dirty draft + a genuinely different committed graph (an
 *                    agent committed while the user had unsaved edits): keep
 *                    the draft, show the actionable conflict toast.
 *
 * The key discriminator (Bug A): a *real* agent conflict is when the server's
 * committed GRAPH diverges from the BASELINE the user loaded — NOT when the
 * draft merely diverges from the server (that's just the user's own dirty
 * edits), and NOT on a meta-only (rename) refetch. The graph comparison
 * EXCLUDES `__meta__` so a rename never reads as a conflict.
 */
import { toast } from 'sonner';

export type SeedDecision = 'seed' | 'meta-merge' | 'conflict';

export interface SeedDecisionInput {
  /** No draft yet (first load). */
  draftIsNull: boolean;
  /** wfId/vKey changed since the last snapshot (strong navigation intent). */
  isNavigation: boolean;
  /** Derived dirty: draft diverges from baseline. */
  dirty: boolean;
  /** True iff the incoming server bytes equal the current draft bytes. */
  serverEqualsDraft: boolean;
  /**
   * True iff the server's COMMITTED GRAPH equals the BASELINE graph, both
   * with `__meta__` stripped. When true, the committed graph has NOT changed
   * externally (no agent commit) — any divergence is the user's own dirty
   * edits, and the refetch only carries new `__meta__` (e.g. a rename).
   */
  serverGraphEqualsBaselineGraph: boolean;
}

export function decideSeed(input: SeedDecisionInput): SeedDecision {
  // First load or route/version navigation: the (new) route owns the draft.
  if (input.draftIsNull || input.isNavigation) return 'seed';
  // Same-route refetch + clean draft: safe to re-seed.
  if (!input.dirty) return 'seed';
  // Dirty draft, but the server echo equals our draft (our own just-saved
  // commit): reconcile silently as clean.
  if (input.serverEqualsDraft) return 'seed';
  // Dirty draft, but the committed GRAPH still equals the baseline graph
  // (`__meta__` stripped): no agent committed — the divergence is the user's
  // own unsaved edits and the refetch only carries new `__meta__` (a rename).
  // Keep the edits, merge the new meta; NO conflict toast.
  if (input.serverGraphEqualsBaselineGraph) return 'meta-merge';
  // Dirty draft + a genuinely different committed graph → agent conflict.
  return 'conflict';
}

export interface ConflictToastArgs {
  /** Existing toast id to update in place (dedupes refetch spam). */
  id: string | number | undefined;
  message: string;
  loadLabel: string;
  keepLabel: string;
  /** Apply the agent version, discarding the user's edits. */
  onLoadAgent: () => void;
  /** Keep the user's edits; just dismiss. */
  onKeepMine: () => void;
}

/**
 * Emit (or update) the ACTIONABLE agent-conflict toast — two buttons:
 * "Load agent version (discard my edits)" and "Keep mine". Replaces the
 * old Infinity-duration no-action dead-end toast. Returns the toast id so
 * the caller can hold it for the next refetch / dismissal.
 */
export function showConflictToast(args: ConflictToastArgs): string | number {
  return toast.warning(args.message, {
    id: args.id,
    duration: Infinity,
    action: { label: args.loadLabel, onClick: args.onLoadAgent },
    cancel: { label: args.keepLabel, onClick: args.onKeepMine },
  });
}
