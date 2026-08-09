import { describe, it, expect } from 'vitest';
import { formatBytes } from '../bytes';

describe('formatBytes', () => {
  it('shows raw bytes below 1 KB', () => {
    expect(formatBytes(0)).toBe('0 B');
    expect(formatBytes(742)).toBe('742 B');
    expect(formatBytes(1023)).toBe('1023 B');
  });

  it('scales to KB/MB/GB by 1024', () => {
    expect(formatBytes(1024)).toBe('1 KB');
    expect(formatBytes(1536)).toBe('1.5 KB');
    expect(formatBytes(1024 * 1024)).toBe('1 MB');
    expect(formatBytes(5 * 1024 * 1024 + 512 * 1024)).toBe('5.5 MB');
    expect(formatBytes(3 * 1024 ** 3)).toBe('3 GB');
  });

  it('trims a trailing .0', () => {
    expect(formatBytes(2048)).toBe('2 KB');
  });

  it('guards against bad input', () => {
    expect(formatBytes(-1)).toBe('0 B');
    expect(formatBytes(NaN)).toBe('0 B');
  });
});
