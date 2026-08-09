/**
 * Stream 8 (M5) — `useUnsavedGuard` blocks navigation + browser unload while
 * the draft is dirty, and passes through when clean.
 *
 * react-router's `useBlocker` is mocked: we capture the predicate the hook
 * passes so we can assert WHEN it blocks (dirty + different pathname), and we
 * return a stub `Blocker` so the hook has something to hand back. The
 * `beforeunload` path is asserted via a spy on `window.addEventListener`.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { renderHook } from '@testing-library/react';

// Capture the blocker predicate the hook installs.
let capturedPredicate: ((args: {
  currentLocation: { pathname: string };
  nextLocation: { pathname: string };
}) => boolean) | boolean | null = null;

const stubBlocker = { state: 'unblocked', proceed: vi.fn(), reset: vi.fn() };

vi.mock('react-router', () => ({
  useBlocker: (
    pred:
      | ((args: {
          currentLocation: { pathname: string };
          nextLocation: { pathname: string };
        }) => boolean)
      | boolean,
  ) => {
    capturedPredicate = pred;
    return stubBlocker;
  },
}));

import { useDirtyNavigationGuard } from '@/lib/navigation/use-dirty-navigation-guard';

function runPredicate(from: string, to: string): boolean {
  if (typeof capturedPredicate === 'function') {
    return capturedPredicate({
      currentLocation: { pathname: from },
      nextLocation: { pathname: to },
    });
  }
  return Boolean(capturedPredicate);
}

beforeEach(() => {
  capturedPredicate = null;
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('useUnsavedGuard — in-app navigation blocking', () => {
  it('blocks a page departure (different pathname) when dirty', () => {
    renderHook(() => useDirtyNavigationGuard(true));
    expect(runPredicate('/workflow/wf1', '/workspace')).toBe(true);
  });

  it('does NOT block when clean', () => {
    renderHook(() => useDirtyNavigationGuard(false));
    expect(runPredicate('/workflow/wf1', '/workspace')).toBe(false);
  });

  it('does NOT block a same-pathname transition even when dirty (query-only change)', () => {
    renderHook(() => useDirtyNavigationGuard(true));
    expect(runPredicate('/workflow/wf1', '/workflow/wf1')).toBe(false);
  });

  it('returns the router Blocker so the caller can render its confirm', () => {
    const { result } = renderHook(() => useDirtyNavigationGuard(true));
    expect(result.current).toBe(stubBlocker);
  });
});

describe('useUnsavedGuard — beforeunload (browser close/refresh)', () => {
  it('registers a beforeunload listener while dirty and removes it on cleanup', () => {
    const add = vi.spyOn(window, 'addEventListener');
    const remove = vi.spyOn(window, 'removeEventListener');

    const { unmount } = renderHook(() => useDirtyNavigationGuard(true));

    const added = add.mock.calls.find(([type]) => type === 'beforeunload');
    expect(added).toBeTruthy();

    // The handler calls preventDefault so the native prompt fires.
    const handler = added![1] as (e: BeforeUnloadEvent) => void;
    const evt = { preventDefault: vi.fn(), returnValue: undefined } as unknown as BeforeUnloadEvent;
    handler(evt);
    expect(evt.preventDefault).toHaveBeenCalled();

    unmount();
    expect(
      remove.mock.calls.some(([type]) => type === 'beforeunload'),
    ).toBe(true);
  });

  it('does NOT register a beforeunload listener when clean', () => {
    const add = vi.spyOn(window, 'addEventListener');
    renderHook(() => useDirtyNavigationGuard(false));
    expect(add.mock.calls.some(([type]) => type === 'beforeunload')).toBe(false);
  });
});
