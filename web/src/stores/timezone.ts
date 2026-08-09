/**
 * Timezone preference store.
 *
 * The user's chosen IANA timezone is a cross-cutting display preference: it
 * affects every human-facing timestamp the app renders (workflow updated time,
 * task submitted time, deployment last-invoked time, …). The backend always
 * stores/serves UTC; the frontend applies this zone at render time.
 *
 * Why a zustand store and not a bare `localStorage` value
 * ------------------------------------------------------
 * `localStorage` is NOT reactive — writing to it does not re-render subscribed
 * components. We mirror the active zone in this tiny store so that
 * `setTimezone()` updates every `useTimezone()` consumer immediately (same
 * reactivity seam the app uses for its other UI state — see `stores/ui.ts`).
 *
 * Persistence lives in `@/lib/timezone` (the `STORAGE_KEY` + `localStorage`
 * read/write), mirroring how `@/lib/i18n` owns locale persistence while the
 * runtime switch flows through i18next. This store is the runtime mirror; the
 * lib module is the source of truth for the persisted value at bootstrap.
 */
import { create } from 'zustand';

export interface TimezoneState {
  /** Active IANA timezone, e.g. `'Asia/Shanghai'` or `'UTC'`. */
  timezone: string;
  /** Replace the active zone (called by `setTimezone` in `@/lib/timezone`). */
  setTimezone: (tz: string) => void;
}

/**
 * Resolve the browser's default IANA zone. Falls back to `'UTC'` if the
 * runtime can't resolve one (extremely rare, but keeps the type non-null).
 */
export function browserTimezone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
  } catch {
    return 'UTC';
  }
}

export const useTimezoneStore = create<TimezoneState>((set) => ({
  // Seeded from localStorage by `@/lib/timezone` at module-init; this default
  // only applies if that bootstrap hasn't run (e.g. in an isolated unit test).
  timezone: browserTimezone(),
  setTimezone: (tz) => set({ timezone: tz }),
}));
