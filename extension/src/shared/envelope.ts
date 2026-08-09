/**
 * The single shared message envelope (§5.4) — TypeScript twin of
 * `api/src/vibecanvas_api/browser/envelope.py`. Encode/decode MUST produce and
 * accept the same JSON shape as the Python side so the two ends interoperate:
 *
 *   { v: 1, kind, id, channel, transport, data, producer }
 *
 * `data` and `producer` default to `null` (the wire form of Python's `None`).
 * Ping, command, and observation traffic share this envelope and WebSocket
 * client.
 */

export type Envelope = {
  v: 1;
  kind: string;
  id: string;
  channel: string;
  transport: string;
  data: unknown;
  producer: string | null;
};

/** Fields that MUST be present for a frame to be a valid envelope. */
const REQUIRED = ["kind", "id", "channel", "transport"] as const;

export function encode(
  kind: string,
  o: {
    id: string;
    channel: string;
    transport: string;
    data?: unknown;
    producer?: string;
  },
): string {
  return JSON.stringify({
    v: 1,
    kind,
    id: o.id,
    channel: o.channel,
    transport: o.transport,
    data: o.data ?? null,
    producer: o.producer ?? null,
  });
}

export function decode(raw: string): Envelope {
  let d: unknown;
  try {
    d = JSON.parse(raw);
  } catch {
    throw new Error("malformed envelope: not JSON");
  }
  if (
    typeof d !== "object" ||
    d === null ||
    REQUIRED.some((k) => !(k in (d as Record<string, unknown>)))
  ) {
    throw new Error("envelope missing required fields");
  }
  return d as Envelope;
}
