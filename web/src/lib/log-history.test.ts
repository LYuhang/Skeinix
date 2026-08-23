import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { resolveLogRange } from '@/lib/log-history';

describe('resolveLogRange', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-08-22T12:00:00.000Z'));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it.each([
    ['1h', '2026-08-22T11:00:00.000Z'],
    ['24h', '2026-08-21T12:00:00.000Z'],
    ['7d', '2026-08-15T12:00:00.000Z'],
    ['30d', '2026-07-23T12:00:00.000Z'],
  ] as const)('resolves the %s preset on the client before sending it to the API', (range, from) => {
    expect(resolveLogRange({ range, from: '', to: '' })).toEqual({ from });
  });

  it('leaves the all-time range unbounded', () => {
    expect(resolveLogRange({ range: 'all', from: '', to: '' })).toEqual({});
  });

  it('normalizes valid custom boundaries and omits invalid or empty values', () => {
    expect(resolveLogRange({
      range: 'custom',
      from: '2026-08-21T08:30',
      to: 'not-a-date',
    })).toEqual({
      from: new Date('2026-08-21T08:30').toISOString(),
      to: undefined,
    });
  });
});
