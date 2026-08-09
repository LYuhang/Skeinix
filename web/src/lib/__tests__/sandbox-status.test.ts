import { describe, expect, it } from 'vitest';
import { formatSandboxTtl, sandboxTtlRemaining } from '@/lib/sandbox-status';

describe('formatSandboxTtl', () => {
  it('formats a sliding sandbox TTL without false precision', () => {
    expect(formatSandboxTtl(1799.2)).toBe('30m');
    expect(formatSandboxTtl(59.1)).toBe('1m');
    expect(formatSandboxTtl(42)).toBe('42s');
    expect(formatSandboxTtl(3600)).toBe('1h');
    expect(formatSandboxTtl(null)).toBeNull();
  });
});

describe('sandboxTtlRemaining', () => {
  it('derives a countdown from the positive idle clock', () => {
    expect(sandboxTtlRemaining({ ttl_s: 300, idle_elapsed_s: 42 })).toBe(258);
    expect(sandboxTtlRemaining({ ttl_s: 300, idle_elapsed_s: 400 })).toBe(0);
  });

  it('does not count down while activity pauses the TTL', () => {
    expect(sandboxTtlRemaining({ ttl_s: 300, idle_elapsed_s: 0, ttl_paused: true })).toBeNull();
  });
});
