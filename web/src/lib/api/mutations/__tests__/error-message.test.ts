/**
 * Unit tests for the shared `errorMessage` helper.
 *
 * Covers the four shapes the helper claims to handle:
 *   1. `Error` instance         → `.message`.
 *   2. `{ detail: string }`     → that string.
 *   3. `{ detail: [{msg}, …] }` → `msg` strings joined with `; `.
 *   4. Fallback `String(e)`     → primitives or unknown objects.
 *
 * This is the toast-text path for every mutation in the UI, so any
 * regression here surfaces in every error banner the user sees.
 */
import { describe, expect, it } from 'vitest';
import { errorMessage } from '@/lib/api/mutations/error-message';

describe('errorMessage', () => {
  it('returns `Error.message` for Error instances', () => {
    expect(errorMessage(new Error('boom'))).toBe('boom');
  });

  it('returns the `detail` string from a FastAPI body', () => {
    expect(errorMessage({ detail: 'workflow not found' })).toBe(
      'workflow not found',
    );
  });

  it('joins a FastAPI validation-error `detail` array via `; `', () => {
    const body = {
      detail: [
        { msg: 'field required', loc: ['body', 'name'] },
        { msg: 'must be a string', loc: ['body', 'tags', 0] },
      ],
    };
    expect(errorMessage(body)).toBe('field required; must be a string');
  });

  it('falls back to String(e) for primitives', () => {
    expect(errorMessage('plain string')).toBe('plain string');
    expect(errorMessage(42)).toBe('42');
    expect(errorMessage(null)).toBe('null');
  });
});
