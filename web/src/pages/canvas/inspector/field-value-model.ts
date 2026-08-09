export class FieldCoercionError extends Error {
  readonly type: string;

  constructor(message: string, type: string) {
    super(message);
    this.name = 'FieldCoercionError';
    this.type = type;
  }
}

export function normalizeFieldType(type: string): string {
  const normalized = String(type || '').trim().toLowerCase();
  if (normalized === 'list') return 'array';
  if (normalized === 'dict' || normalized === 'map' || normalized === 'json') return 'object';
  if (normalized === 'bool') return 'boolean';
  if (normalized === 'int') return 'integer';
  return normalized;
}

export function isNumberType(type: string): boolean {
  const normalized = normalizeFieldType(type);
  return normalized === 'number' || normalized === 'integer' || normalized === 'float';
}

export function isJsonType(type: string): boolean {
  const normalized = normalizeFieldType(type);
  return normalized === 'object' || normalized === 'array';
}

export function coerceValueForType(raw: unknown, type: string): unknown {
  const normalizedType = normalizeFieldType(type);
  if (isNumberType(normalizedType)) {
    if (typeof raw === 'number') return raw;
    const value = typeof raw === 'string' ? raw.trim() : '';
    if (!value) throw new FieldCoercionError('Enter a number.', type);
    const number = normalizedType === 'integer' ? Number.parseInt(value, 10) : Number(value);
    if (!Number.isFinite(number)) {
      throw new FieldCoercionError(`"${value}" is not a valid number.`, type);
    }
    return number;
  }
  if (normalizedType === 'boolean') {
    if (typeof raw === 'boolean') return raw;
    if (typeof raw === 'string') return raw.trim().toLowerCase() === 'true';
    return Boolean(raw);
  }
  if (isJsonType(normalizedType)) {
    if (raw !== null && typeof raw === 'object') {
      if (normalizedType === 'array' && !Array.isArray(raw)) {
        throw new FieldCoercionError('Enter a JSON array.', type);
      }
      if (normalizedType === 'object' && Array.isArray(raw)) {
        throw new FieldCoercionError('Enter a JSON object.', type);
      }
      return raw;
    }
    const value = typeof raw === 'string' ? raw.trim() : '';
    if (!value) return normalizedType === 'array' ? [] : {};
    try {
      const parsed: unknown = JSON.parse(value);
      if (normalizedType === 'array' && !Array.isArray(parsed)) {
        throw new FieldCoercionError('Enter a JSON array.', type);
      }
      if (
        normalizedType === 'object' &&
        (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed))
      ) {
        throw new FieldCoercionError('Enter a JSON object.', type);
      }
      return parsed;
    } catch (error) {
      if (error instanceof FieldCoercionError) throw error;
      throw new FieldCoercionError(`Invalid JSON for ${normalizedType}.`, type);
    }
  }
  if (raw === undefined || raw === null) return '';
  return typeof raw === 'string' ? raw : String(raw);
}

export function valueToDisplayString(value: unknown, type: string): string {
  if (value === undefined || value === null) return '';
  if (isJsonType(type) && typeof value === 'object') return JSON.stringify(value, null, 2);
  return typeof value === 'string' ? value : String(value);
}
