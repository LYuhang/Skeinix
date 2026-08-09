import { describe, expect, it } from 'vitest';

import {
  SseEventSequence,
  SseSequenceGapError,
} from '@/lib/api/sse/event-sequence';

describe('SseEventSequence', () => {
  it('advances the resume cursor only after projection is acknowledged', () => {
    const sequence = new SseEventSequence();

    expect(sequence.receive('1')).toEqual({ seq: 1, duplicate: false });
    expect(sequence.cursor).toBe(0);

    sequence.acknowledge(1);
    expect(sequence.cursor).toBe(1);
  });

  it('rejects a wire-order gap and reaccepts the missing frame after rollback', () => {
    const sequence = new SseEventSequence(4);
    expect(sequence.receive('5')).toEqual({ seq: 5, duplicate: false });
    expect(() => sequence.receive('7')).toThrowError(SseSequenceGapError);

    sequence.rollbackUnacknowledged();
    expect(sequence.cursor).toBe(4);
    expect(sequence.receive('5')).toEqual({ seq: 5, duplicate: false });
  });

  it('deduplicates frames already included in the projected cursor', () => {
    const sequence = new SseEventSequence(8);
    expect(sequence.receive('8')).toEqual({ seq: 8, duplicate: true });
    expect(sequence.receive('7')).toEqual({ seq: 7, duplicate: true });
    expect(sequence.receive('')).toEqual({ seq: null, duplicate: false });
  });
});
