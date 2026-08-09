/**
 * Page-local coordination for a Chat's live Turn projection.
 *
 * A newly submitted Turn is streamed by the POST request. Reconciliation may
 * discover that same server-side Run while the POST is still open, but it must
 * not also open the GET replay stream: both transports carry the same durable
 * sequence and appending their `message_delta` frames corrupts the live text.
 *
 * The lease prevents concurrent POST/replay consumers within one page. The
 * event fence is a second line of defence for hand-off races: a durable event
 * sequence may be projected at most once even if an old transport delivers a
 * final buffered frame after its lease was released.
 */

export type TurnStreamKind = 'submission' | 'resume';

export interface TurnStreamLease {
  readonly key: string;
  readonly token: symbol;
  readonly kind: TurnStreamKind;
}

const streamOwners = new Map<string, TurnStreamLease>();
const eventHighWaterMarks = new Map<string, number>();
const MAX_EVENT_FENCES = 256;

function chatStreamKey(wfId: string, chatId: string): string {
  return `${wfId}\u0000${chatId}`;
}

function turnEventKey(wfId: string, chatId: string, turnId: string): string {
  return `${chatStreamKey(wfId, chatId)}\u0000${turnId}`;
}

export function tryAcquireTurnStream(
  wfId: string,
  chatId: string,
  kind: TurnStreamKind,
): TurnStreamLease | null {
  const key = chatStreamKey(wfId, chatId);
  if (streamOwners.has(key)) return null;
  const lease: TurnStreamLease = {
    key,
    token: Symbol(`${kind}:${chatId}`),
    kind,
  };
  streamOwners.set(key, lease);
  return lease;
}

export function releaseTurnStream(lease: TurnStreamLease): void {
  if (streamOwners.get(lease.key)?.token === lease.token) {
    streamOwners.delete(lease.key);
  }
}

/**
 * Reserve one durable SSE sequence number for projection.
 *
 * Each transport still validates contiguity with `SseEventSequence`; this
 * shared high-water mark only rejects a frame already accepted through another
 * transport. Frames without a numeric id are legacy/non-durable and cannot be
 * fenced here.
 */
export function reserveTurnEvent(
  wfId: string,
  chatId: string,
  turnId: string,
  seq: number | null,
): boolean {
  if (seq === null) return true;
  const key = turnEventKey(wfId, chatId, turnId);
  const previous = eventHighWaterMarks.get(key) ?? 0;
  if (seq <= previous) return false;

  // Reinsert to keep Map iteration order as a tiny LRU. Completed Turn fences
  // remain briefly so a late callback from the relinquished transport cannot
  // project after the successor has taken ownership.
  eventHighWaterMarks.delete(key);
  eventHighWaterMarks.set(key, seq);
  while (eventHighWaterMarks.size > MAX_EVENT_FENCES) {
    const oldest = eventHighWaterMarks.keys().next().value as string | undefined;
    if (oldest === undefined) break;
    eventHighWaterMarks.delete(oldest);
  }
  return true;
}

/**
 * Return the sequence already accepted by this page for a specific Turn.
 *
 * Unlike the localStorage cursor, this value cannot survive a reload. It is
 * therefore safe for same-page POST → replay hand-off while a freshly loaded
 * page still rebuilds its empty projection from sequence zero.
 */
export function getReservedTurnEventCursor(
  wfId: string,
  chatId: string,
  turnId: string,
): number {
  return eventHighWaterMarks.get(turnEventKey(wfId, chatId, turnId)) ?? 0;
}
