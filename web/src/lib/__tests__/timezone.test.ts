/**
 * Unit tests for the timezone preference module.
 *
 * Focus:
 *   - `formatDateTime` interprets its input as a UTC instant and renders the
 *     correct WALL-CLOCK time in a fixed target zone (the core UTC→local
 *     contract), for both epoch-seconds and ISO-string inputs.
 *   - null/invalid input degrades to the placeholder.
 *   - `setTimezone` persists to localStorage AND updates the store.
 */
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import {
  STORAGE_KEY,
  formatDateTime,
  getTimezone,
  isValidTimezone,
  setTimezone,
} from '@/lib/timezone';
import { useTimezoneStore } from '@/stores/timezone';

// A fixed instant: 2026-06-11T12:00:00Z (UTC noon).
const UTC_NOON_ISO = '2026-06-11T12:00:00Z';
const UTC_NOON_EPOCH_SECONDS = Math.floor(Date.parse(UTC_NOON_ISO) / 1000);

describe('formatDateTime', () => {
  beforeEach(() => {
    // Pin the active zone via the store so tests are deterministic regardless
    // of the runner's TZ. We pass explicit opts below too, but this also
    // exercises the default-zone read.
    useTimezoneStore.getState().setTimezone('UTC');
  });

  it('renders a UTC instant at the correct wall-clock in a target zone (ISO input)', () => {
    // Asia/Shanghai is UTC+8 with no DST → 12:00 UTC = 20:00 local.
    const out = formatDateTime(UTC_NOON_ISO, {
      timeZone: 'Asia/Shanghai',
      locale: 'en-US',
      dateStyle: undefined,
      timeStyle: 'short',
    });
    expect(out).toContain('8:00');
    expect(out).toContain('PM');
  });

  it('treats a number input as epoch SECONDS (UTC) and converts', () => {
    // Same instant via epoch seconds → New York is UTC-4 in June (EDT) → 08:00.
    const out = formatDateTime(UTC_NOON_EPOCH_SECONDS, {
      timeZone: 'America/New_York',
      locale: 'en-US',
      dateStyle: undefined,
      timeStyle: 'short',
    });
    expect(out).toContain('8:00');
    expect(out).toContain('AM');
  });

  it('renders UTC itself unchanged', () => {
    const out = formatDateTime(UTC_NOON_ISO, {
      timeZone: 'UTC',
      locale: 'en-US',
      dateStyle: undefined,
      timeStyle: 'short',
    });
    expect(out).toContain('12:00');
    expect(out).toContain('PM');
  });

  it('returns the placeholder for null / undefined / empty / invalid input', () => {
    expect(formatDateTime(null)).toBe('—');
    expect(formatDateTime(undefined)).toBe('—');
    expect(formatDateTime('')).toBe('—');
    expect(formatDateTime('not-a-date')).toBe('—');
    expect(formatDateTime(null, { placeholder: 'N/A' })).toBe('N/A');
  });

  it('accepts a Date instance', () => {
    const out = formatDateTime(new Date(UTC_NOON_ISO), {
      timeZone: 'UTC',
      locale: 'en-US',
      dateStyle: undefined,
      timeStyle: 'short',
    });
    expect(out).toContain('12:00');
  });
});

describe('isValidTimezone', () => {
  it('accepts known IANA zones and rejects garbage', () => {
    expect(isValidTimezone('UTC')).toBe(true);
    expect(isValidTimezone('Asia/Shanghai')).toBe(true);
    expect(isValidTimezone('Not/AZone')).toBe(false);
    expect(isValidTimezone('')).toBe(false);
  });
});

describe('setTimezone', () => {
  afterEach(() => {
    localStorage.removeItem(STORAGE_KEY);
  });

  it('persists to localStorage AND updates the store', () => {
    setTimezone('Europe/London');
    expect(localStorage.getItem(STORAGE_KEY)).toBe('Europe/London');
    expect(getTimezone()).toBe('Europe/London');
    expect(useTimezoneStore.getState().timezone).toBe('Europe/London');
  });

  it('ignores an invalid zone (keeps the current value)', () => {
    setTimezone('America/Los_Angeles');
    setTimezone('Bogus/Zone');
    expect(getTimezone()).toBe('America/Los_Angeles');
  });
});
