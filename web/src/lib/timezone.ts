/**
 * Timezone preference: persistence + UTC→local timestamp formatting.
 *
 * The backend stores/serves every timestamp in UTC. The frontend renders all
 * human-facing times in the USER'S chosen IANA timezone. This module owns:
 *   - the persisted preference (`localStorage[STORAGE_KEY]`, mirroring how
 *     `@/lib/i18n` owns the locale key);
 *   - the runtime mirror in `@/stores/timezone` (the reactivity seam — see
 *     that file's header for why a plain localStorage value won't re-render);
 *   - the formatter(s) that turn a UTC timestamp into a wall-clock string in
 *     the active zone, honouring the active i18n locale.
 *
 * Input shapes accepted by the formatter (the codebase has both):
 *   - epoch SECONDS  (workflow `updated_at` / `created_at`, inspector meta);
 *   - ISO-8601 strings (task `submitted_at`, deployment `last_invoked_at`, …);
 *   - `Date` objects.
 * All are interpreted as UTC instants and rendered in the chosen zone.
 *
 * Usage
 * -----
 *   import { formatDateTime } from '@/lib/timezone';           // one-shot
 *   const fmt = useFormatDateTime();                            // reactive hook
 *   <td>{fmt(wf.updated_at)}</td>
 *
 * The plain `formatDateTime` reads the CURRENT zone from the store, so it is
 * correct at call time but a component using it directly won't re-render when
 * the zone changes. Components that must update live use `useFormatDateTime()`
 * (or `useTimezone()`), which subscribe to the store.
 */
import i18n from '@/lib/i18n';
import {
  browserTimezone,
  useTimezoneStore,
  type TimezoneState,
} from '@/stores/timezone';

export const STORAGE_KEY = 'vibecanvas.timezone';

/**
 * Resolve the persisted zone, or the browser default. Validated against the
 * runtime: a stale/garbage stored value (e.g. a zone the browser no longer
 * knows) falls back to the browser default rather than throwing later in
 * `Intl.DateTimeFormat`.
 */
function bootstrapTimezone(): string {
  const stored = (() => {
    try {
      return localStorage.getItem(STORAGE_KEY);
    } catch {
      return null;
    }
  })();
  if (stored && isValidTimezone(stored)) return stored;
  return browserTimezone();
}

/** True iff `tz` is an IANA zone the current runtime can format with. */
export function isValidTimezone(tz: string): boolean {
  if (!tz) return false;
  try {
    // Throws RangeError for an unknown/invalid zone.
    new Intl.DateTimeFormat('en-US', { timeZone: tz });
    return true;
  } catch {
    return false;
  }
}

/** The browser's default IANA zone (re-exported for the Settings list). */
export { browserTimezone };

/** Current active zone (non-reactive read — fine for one-shot formatting). */
export function getTimezone(): string {
  return useTimezoneStore.getState().timezone;
}

/**
 * Persist a new zone AND update the store so subscribed components re-render
 * immediately. Mirrors `setLocale` in `@/lib/i18n`: localStorage write +
 * runtime switch co-located. Ignores invalid input (keeps current zone).
 */
export function setTimezone(tz: string): void {
  if (!isValidTimezone(tz)) return;
  try {
    localStorage.setItem(STORAGE_KEY, tz);
  } catch {
    // Private-mode / quota — keep the in-memory switch working regardless.
  }
  useTimezoneStore.getState().setTimezone(tz);
}

/** React hook: the active zone, subscribed to the store (re-renders on change). */
export function useTimezone(): string {
  return useTimezoneStore((s: TimezoneState) => s.timezone);
}

/**
 * Accepted timestamp inputs. Numbers are epoch SECONDS (backend convention);
 * strings are ISO-8601; `Date` is passed through.
 */
export type TimestampInput = number | string | Date | null | undefined;

export interface FormatDateTimeOptions {
  /** Empty/invalid placeholder. Default `'—'`. */
  placeholder?: string;
  /** `Intl` date style. Default `'medium'`. Pass `undefined` to omit the date. */
  dateStyle?: 'full' | 'long' | 'medium' | 'short';
  /** `Intl` time style. Default `'short'`. Pass `undefined` to omit the time. */
  timeStyle?: 'full' | 'long' | 'medium' | 'short';
  /** Override the zone (defaults to the active preference). */
  timeZone?: string;
  /** Override the locale (defaults to the active i18n language). */
  locale?: string;
}

/** Normalise the various input shapes into a UTC `Date`, or `null` if invalid. */
function toDate(input: TimestampInput): Date | null {
  if (input === null || input === undefined || input === '') return null;
  let d: Date;
  if (input instanceof Date) {
    d = input;
  } else if (typeof input === 'number') {
    // Epoch seconds → ms.
    d = new Date(input * 1000);
  } else {
    d = new Date(input);
  }
  return Number.isNaN(d.getTime()) ? null : d;
}

/**
 * Format a UTC timestamp into a wall-clock string in the active zone + locale.
 * Robust to null/invalid input (returns the placeholder). Non-reactive: reads
 * the current zone at call time. For live updates use `useFormatDateTime()`.
 */
export function formatDateTime(
  input: TimestampInput,
  opts: FormatDateTimeOptions = {},
): string {
  const placeholder = opts.placeholder ?? '—';
  const d = toDate(input);
  if (!d) return placeholder;

  const timeZone = opts.timeZone ?? getTimezone();
  const locale = opts.locale ?? i18n.resolvedLanguage ?? i18n.language;
  const dateStyle = 'dateStyle' in opts ? opts.dateStyle : 'medium';
  const timeStyle = 'timeStyle' in opts ? opts.timeStyle : 'short';

  try {
    return new Intl.DateTimeFormat(locale, {
      timeZone,
      ...(dateStyle ? { dateStyle } : {}),
      ...(timeStyle ? { timeStyle } : {}),
    }).format(d);
  } catch {
    // Unknown zone slipped through — degrade to the browser default rather
    // than rendering the placeholder for a perfectly valid instant.
    try {
      return new Intl.DateTimeFormat(locale, {
        timeZone: browserTimezone(),
        ...(dateStyle ? { dateStyle } : {}),
        ...(timeStyle ? { timeStyle } : {}),
      }).format(d);
    } catch {
      return placeholder;
    }
  }
}

/**
 * React hook returning a `formatDateTime` bound to the active zone, subscribed
 * to the store so consumers re-render the moment the user changes timezone.
 */
export function useFormatDateTime(): (
  input: TimestampInput,
  opts?: FormatDateTimeOptions,
) => string {
  const timeZone = useTimezone();
  return (input, opts) => formatDateTime(input, { timeZone, ...opts });
}

// ── Bootstrap ───────────────────────────────────────────────────────────────
// Seed the store from localStorage at module init so the very first render
// already uses the saved zone. Imported for this side effect via the lib's
// public API (any `formatDateTime`/`useTimezone` consumer triggers it).
useTimezoneStore.getState().setTimezone(bootstrapTimezone());
