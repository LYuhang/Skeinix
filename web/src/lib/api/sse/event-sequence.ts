/**
 * Tracks the difference between an SSE frame being received and its UI
 * projection being committed.
 *
 * Only `acknowledge()` may advance the durable Last-Event-ID cursor. This is
 * deliberately separate from `receive()`: JSON parsing, state projection, or a
 * page reload can happen after bytes arrive but before the user has actually
 * seen the event. Reconnecting from the last acknowledged id makes that window
 * replay-safe.
 */
export class SseSequenceGapError extends Error {
  readonly expected: number;
  readonly received: number;

  constructor(expected: number, received: number) {
    super(`SSE event sequence gap: expected ${expected}, received ${received}`);
    this.name = 'SseSequenceGapError';
    this.expected = expected;
    this.received = received;
  }
}

export interface ReceivedSseSequence {
  /** `null` for heartbeats or legacy frames without a numeric SSE id. */
  seq: number | null;
  /** A replayed frame already included in the acknowledged projection. */
  duplicate: boolean;
}

export class SseEventSequence {
  private acknowledged: number;
  private received: number;

  constructor(cursor = 0) {
    const safeCursor = Number.isSafeInteger(cursor) && cursor > 0 ? cursor : 0;
    this.acknowledged = safeCursor;
    this.received = safeCursor;
  }

  get cursor(): number {
    return this.acknowledged;
  }

  receive(rawId: string | undefined | null): ReceivedSseSequence {
    if (!rawId) return { seq: null, duplicate: false };
    const seq = Number(rawId);
    if (!Number.isSafeInteger(seq) || seq <= 0) {
      return { seq: null, duplicate: false };
    }
    if (seq <= this.acknowledged || seq <= this.received) {
      return { seq, duplicate: true };
    }
    const expected = this.received + 1;
    if (seq !== expected) throw new SseSequenceGapError(expected, seq);
    this.received = seq;
    return { seq, duplicate: false };
  }

  acknowledge(seq: number | null): number {
    if (seq !== null && seq > this.acknowledged && seq <= this.received) {
      this.acknowledged = seq;
    }
    return this.acknowledged;
  }

  /** Forget received-but-not-projected frames before opening a replay stream. */
  rollbackUnacknowledged(): void {
    this.received = this.acknowledged;
  }
}
