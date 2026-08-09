/**
 * Shared `errorMessage` helper for mutation toasts.
 *
 * openapi-fetch rejects the raw response body for non-2xx responses —
 * typically a FastAPI `HTTPValidationError` shaped as
 * `{ detail: string | { msg: string }[] }` — which is not an `Error`
 * instance. A naive `String(e)` against that body produces the famously
 * unhelpful `[object Object]`. Centralizing the coercion here keeps every
 * mutation file's `onError` handler one-liner and consistent.
 *
 * Handled shapes (in order):
 *   1. `Error` instance → `e.message`.
 *   2. Object with `detail: string` → that string.
 *   3. Object with `detail: { msg }[] | unknown[]` → joined with `; `.
 *   4. Fallback `String(e)` for primitives / unknowns.
 *
 * Originally inlined in `mutations/workflows.ts`. Extracted as part of
 * CanvasToolbar introduces the second consumer in
 * `mutations/workflow-ops.ts`.
 */
export const errorMessage = (e: unknown): string => {
  if (e instanceof Error) return e.message;
  if (typeof e === 'object' && e !== null) {
    const detail = (e as { detail?: unknown }).detail;
    if (typeof detail === 'string') return detail;
    if (Array.isArray(detail)) {
      return detail
        .map((item) => {
          if (typeof item === 'object' && item !== null && 'msg' in item) {
            return String((item as { msg: unknown }).msg);
          }
          return String(item);
        })
        .join('; ');
    }
  }
  return String(e);
};

/** A loaded resource that becomes unavailable commonly indicates live revoke. */
export const isAuthorizationChangedError = (e: unknown): boolean => {
  const message = errorMessage(e).toLowerCase();
  return message === 'resource_not_found'
    || message.includes('permission')
    || message.includes('authorization');
};
