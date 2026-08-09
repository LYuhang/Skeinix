/**
 * UX-12 regression guard. The double-click-add-at-origin bug was NOT a logic
 * bug in the cards — it was a PROVIDER PLACEMENT bug: the Explorer palette
 * (such as NodeCard) renders as a SIBLING of the canvas (in AppLayout),
 * OUTSIDE the old per-CanvasPage provider, so `useCanvasViewport()` read the
 * default `() => null` getter and `addNode` fell back to the flow origin.
 *
 * The fix lifts a self-contained `CanvasViewportProvider` to the common
 * ancestor: the canvas REGISTERS its center getter (via
 * `useRegisterViewportCenter`) and a SIBLING consumer reads it (via
 * `useCanvasViewport`). This test reproduces that sibling topology and asserts
 * the registered getter is visible across the subtree — i.e. a sibling no
 * longer gets the null default.
 */
import { describe, it, expect } from 'vitest';
import { render, screen, act } from '@testing-library/react';
import {
  CanvasViewportProvider,
  useCanvasViewport,
  useRegisterViewportCenter,
} from '@/pages/canvas/CanvasViewportContext';

const CENTER = { x: 320, y: 180 };

// Stands in for `Canvas`: registers a center getter on mount.
function Registrar() {
  const register = useRegisterViewportCenter();
  register(() => CENTER);
  return null;
}

// Stands in for an Explorer palette card (a SIBLING of the canvas).
function SiblingConsumer() {
  const { viewportCenterFlowPos } = useCanvasViewport();
  const pos = viewportCenterFlowPos();
  return <div data-testid="pos">{pos ? `${pos.x},${pos.y}` : 'null'}</div>;
}

describe('CanvasViewportProvider — sibling registration (UX-12)', () => {
  it('exposes the canvas-registered center getter to a sibling consumer', () => {
    render(
      <CanvasViewportProvider>
        <Registrar />
        <SiblingConsumer />
      </CanvasViewportProvider>,
    );
    // Registrar ran during render; the stable read value calls through the ref.
    act(() => {});
    expect(screen.getByTestId('pos').textContent).toBe('320,180');
  });

  it('a consumer with NO provider gets the null default (origin fallback)', () => {
    // The OLD bug: cards outside the provider. The default getter returns null,
    // which the cards turn into the `{x:0,y:0}` origin fallback.
    render(<SiblingConsumer />);
    expect(screen.getByTestId('pos').textContent).toBe('null');
  });
});
