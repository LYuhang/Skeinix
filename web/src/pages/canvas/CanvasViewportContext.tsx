/* eslint-disable react-refresh/only-export-components -- Context providers intentionally co-locate their consumer hooks. */
import { createContext, useCallback, useContext, useMemo, useRef, type ReactNode } from 'react';

export interface CanvasViewport {
  /** Flow-coord center of the visible canvas pane, or null if unavailable. */
  viewportCenterFlowPos: () => { x: number; y: number } | null;
}

/** A getter returning the flow-coord center of the visible canvas pane. */
type ViewportCenterGetter = () => { x: number; y: number } | null;

const CanvasViewportContext = createContext<CanvasViewport>({ viewportCenterFlowPos: () => null });

/**
 * Lets a deep descendant (`Canvas`) register the live "viewport center"
 * getter while the READ context ({@link CanvasViewportContext}) is consumed by
 * SIBLINGS of the canvas — notably the Explorer's node/template palette cards.
 *
 * This MUST be its own context (not threaded as a prop) because the palette
 * lives in `AppLayout`, OUTSIDE the `<Outlet>` that renders `CanvasPage` →
 * `Canvas`. Both contexts are therefore provided ABOVE the Outlet (in
 * `AppLayout`), so the palette reads a populated getter instead of the default
 * `() => null` that previously made double-click-add fall back to the origin.
 */
const RegisterViewportContext = createContext<(fn: ViewportCenterGetter) => void>(() => {});

/**
 * Owns the viewport-center ref and provides BOTH contexts so the palette
 * (sibling of the canvas) and the canvas (which registers the getter) can be
 * wrapped together at a common ancestor (`AppLayout`).
 *
 * The exposed read value is a STABLE object that calls through the ref, so
 * consumers don't re-render as the viewport pans/zooms — they only invoke the
 * getter lazily at double-click time.
 */
export function CanvasViewportProvider({ children }: { children: ReactNode }) {
  const fnRef = useRef<ViewportCenterGetter>(() => null);
  const value = useMemo<CanvasViewport>(
    () => ({ viewportCenterFlowPos: () => fnRef.current() }),
    [],
  );
  const register = useCallback((fn: ViewportCenterGetter) => {
    fnRef.current = fn;
  }, []);
  return (
    <CanvasViewportContext.Provider value={value}>
      <RegisterViewportContext.Provider value={register}>
        {children}
      </RegisterViewportContext.Provider>
    </CanvasViewportContext.Provider>
  );
}

export const useCanvasViewport = () => useContext(CanvasViewportContext);

/** Canvas calls this with its `screenToFlowPosition`-backed center getter. */
export const useRegisterViewportCenter = () => useContext(RegisterViewportContext);
