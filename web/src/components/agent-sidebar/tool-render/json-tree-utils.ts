export const INITIAL_EXPAND_DEPTH = 2;
export const MAX_NODES = 500;

export type JsonValue = unknown;

export function parseJson(
  data: string | undefined,
): { ok: true; value: JsonValue } | { ok: false } {
  if (typeof data !== 'string' || data.trim().length === 0) return { ok: false };
  try {
    return { ok: true, value: JSON.parse(data) };
  } catch {
    return { ok: false };
  }
}

export function countNodes(value: JsonValue, limit = MAX_NODES * 4): number {
  let count = 0;
  const stack: JsonValue[] = [value];
  while (stack.length > 0 && count <= limit) {
    const current = stack.pop();
    count += 1;
    if (Array.isArray(current)) {
      stack.push(...current);
    } else if (current && typeof current === 'object') {
      stack.push(...Object.values(current as Record<string, JsonValue>));
    }
  }
  return count;
}
