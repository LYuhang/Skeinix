/**
 * Stream 0e — agent ↔ manual-edit reconciliation policy.
 *
 * Drives the pure `decideSeed` the CanvasPage effect uses to choose between
 * re-seeding the draft and showing the actionable conflict toast.
 */
import { describe, expect, it, vi, beforeEach } from 'vitest';

const warn = vi.fn((_msg: string, _opts: Record<string, any>) => 'toast-1');
vi.mock('sonner', () => ({
  toast: { warning: (msg: string, opts: Record<string, any>) => warn(msg, opts) },
}));

import { decideSeed, showConflictToast } from '@/pages/canvas/seedPolicy';

beforeEach(() => warn.mockClear());

describe('decideSeed', () => {
  it('first load (no draft) → seed', () => {
    expect(
      decideSeed({
        draftIsNull: true,
        isNavigation: true,
        dirty: false,
        serverEqualsDraft: false,
        serverGraphEqualsBaselineGraph: false,
      }),
    ).toBe('seed');
  });

  it('route/version navigation while dirty → HARD re-seed (route intent wins)', () => {
    expect(
      decideSeed({
        draftIsNull: false,
        isNavigation: true,
        dirty: true,
        serverEqualsDraft: false,
        serverGraphEqualsBaselineGraph: false,
      }),
    ).toBe('seed');
  });

  it('same-route refetch + clean draft → seed (agent edit while idle / invalidation)', () => {
    expect(
      decideSeed({
        draftIsNull: false,
        isNavigation: false,
        dirty: false,
        serverEqualsDraft: false,
        serverGraphEqualsBaselineGraph: false,
      }),
    ).toBe('seed');
  });

  it('same-route refetch + dirty + server echo of our own commit → seed (no conflict)', () => {
    // After markSaved, the server echo equals the draft bytes → reconcile clean.
    expect(
      decideSeed({
        draftIsNull: false,
        isNavigation: false,
        dirty: true,
        serverEqualsDraft: true,
        serverGraphEqualsBaselineGraph: true,
      }),
    ).toBe('seed');
  });

  it('Bug A: dirty + rename refetch (committed graph == baseline graph) → meta-merge, NO conflict', () => {
    // The user has unsaved GRAPH edits (dirty, serverEqualsDraft=false) AND
    // renamed the workflow. The PATCH refetch carries new __meta__ but the
    // committed graph still equals the loaded baseline graph → merge meta,
    // keep the edits, NO toast.
    expect(
      decideSeed({
        draftIsNull: false,
        isNavigation: false,
        dirty: true,
        serverEqualsDraft: false,
        serverGraphEqualsBaselineGraph: true,
      }),
    ).toBe('meta-merge');
  });

  it('Bug A: dirty + a genuinely different committed graph → conflict (actionable toast)', () => {
    // An agent committed a different graph while the user had unsaved edits:
    // the committed graph diverges from the baseline graph → real conflict.
    expect(
      decideSeed({
        draftIsNull: false,
        isNavigation: false,
        dirty: true,
        serverEqualsDraft: false,
        serverGraphEqualsBaselineGraph: false,
      }),
    ).toBe('conflict');
  });
});

describe('showConflictToast — ACTIONABLE (two buttons)', () => {
  it('emits a warning with both an action and a cancel button wired to callbacks', () => {
    const onLoadAgent = vi.fn();
    const onKeepMine = vi.fn();
    showConflictToast({
      id: undefined,
      message: 'Agent changed it',
      loadLabel: 'Load agent version',
      keepLabel: 'Keep mine',
      onLoadAgent,
      onKeepMine,
    });

    expect(warn).toHaveBeenCalledTimes(1);
    const [msg, opts] = warn.mock.calls[0]!;
    expect(msg).toBe('Agent changed it');
    expect(opts.duration).toBe(Infinity);
    expect(opts.action.label).toBe('Load agent version');
    expect(opts.cancel.label).toBe('Keep mine');

    // Buttons are wired to the supplied callbacks.
    opts.action.onClick();
    expect(onLoadAgent).toHaveBeenCalledTimes(1);
    opts.cancel.onClick();
    expect(onKeepMine).toHaveBeenCalledTimes(1);
  });

  it('passes the existing toast id through so refetches update in place', () => {
    showConflictToast({
      id: 'toast-7',
      message: 'm',
      loadLabel: 'l',
      keepLabel: 'k',
      onLoadAgent: () => {},
      onKeepMine: () => {},
    });
    const [, opts] = warn.mock.calls[0]!;
    expect(opts.id).toBe('toast-7');
  });
});
