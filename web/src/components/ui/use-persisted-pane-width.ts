import { useCallback, useEffect, useState } from 'react';

interface PersistedPaneWidthOptions {
  storageKey: string;
  defaultWidth: number;
  minWidth: number;
  maxWidth: number;
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

export function usePersistedPaneWidth({
  storageKey,
  defaultWidth,
  minWidth,
  maxWidth,
}: PersistedPaneWidthOptions) {
  const [width, setWidthState] = useState(() => {
    if (typeof window === 'undefined') return defaultWidth;
    try {
      const stored = Number(window.localStorage.getItem(storageKey));
      return Number.isFinite(stored) && stored > 0
        ? clamp(stored, minWidth, maxWidth)
        : defaultWidth;
    } catch {
      return defaultWidth;
    }
  });

  const setWidth = useCallback((nextWidth: number) => {
    const next = clamp(nextWidth, minWidth, maxWidth);
    setWidthState(next);
  }, [maxWidth, minWidth]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      try {
        window.localStorage.setItem(storageKey, String(width));
      } catch {
        // Resizing remains available when device-local storage is unavailable.
      }
    }, 160);
    return () => window.clearTimeout(timer);
  }, [storageKey, width]);

  const resetWidth = useCallback(() => setWidth(defaultWidth), [defaultWidth, setWidth]);
  return { width, setWidth, resetWidth };
}
