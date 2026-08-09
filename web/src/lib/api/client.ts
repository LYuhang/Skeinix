/**
 * Typed API client for the vibecanvas-api backend.
 *
 * Wraps `openapi-fetch` with two middleware concerns:
 *   - onRequest: sends the HttpOnly Session cookie and double-submit CSRF
 *     header through the shared session fetch transport.
 *   - onResponse: any 401 triggers `handle401`, which clears the local
 *     authenticated projection and returns to login. The original 401 response
 *     is still returned so callers can surface it (e.g. TanStack Query
 *     turns it into an error state).
 *
 * `baseUrl` reads from `VITE_API_BASE`. An empty string means "same origin",
 * which matches the dev proxy configured in `vite.config.ts`.
 *
 * Types are sourced from the generated `schema.d.ts`. Re-running
 * `pnpm codegen:api:offline` is the only way to update them.
 */
import createClient, { type Middleware } from 'openapi-fetch';
import type { paths } from '@/lib/api/schema';
import { useAuthStore } from '@/stores/auth';
import { getApiBase } from '@/lib/base-path';
import { readSessionCsrfToken, sessionFetch } from '@/lib/api/session-fetch';

const authMiddleware: Middleware = {
  onRequest: ({ request }) => {
    if (['POST', 'PUT', 'PATCH', 'DELETE'].includes(request.method.toUpperCase())) {
      const csrf = readSessionCsrfToken();
      if (csrf) request.headers.set('X-CSRF-Token', csrf);
    }
    return request;
  },
  onResponse: ({ response }) => {
    if (response.status === 401) {
      useAuthStore.getState().handle401();
    }
    return response;
  },
};

export const apiClient = createClient<paths>({
  baseUrl: getApiBase(),
  fetch: sessionFetch,
  credentials: 'include',
});

apiClient.use(authMiddleware);
