import { describe, expect, it } from 'vitest';
import { isSseDoneSentinel, parseSseJson } from '../json';

describe('SSE JSON helpers', () => {
  it('treats [DONE] as a transport sentinel, not JSON payload', () => {
    expect(isSseDoneSentinel('[DONE]')).toBe(true);
    expect(isSseDoneSentinel('  [DONE]  ')).toBe(true);
    expect(parseSseJson('[DONE]')).toEqual({});
  });

  it('parses normal JSON frames', () => {
    expect(parseSseJson('{"status":"completed"}')).toEqual({ status: 'completed' });
  });
});
