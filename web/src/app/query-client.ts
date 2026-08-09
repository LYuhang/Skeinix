/**
 * Module-scope `QueryClient` singleton.
 *
 * Lives in its own file (not `providers.tsx`) so that:
 *   - React Refresh / Vite HMR can keep "component-only" semantics for
 *     `providers.tsx` — exporting a non-component there trips
 *     `react-refresh/only-export-components` and disables fast refresh
 *     for any consumer that imports it.
 *   - Non-React modules (the SSE signal router, T10+) can import the
 *     client without dragging the provider tree into their dependency
 *     graph.
 *
 * The defaults below match the legacy app:
 *   - `staleTime: 30_000`     — small grace window so route bounce-back
 *                               doesn't re-fetch within 30 s.
 *   - `retry: 3`              — three transient retries on queries.
 *   - `refetchOnWindowFocus`  — disabled; we trust SSE to push fresh data
 *                               and don't want a tab-switch storm of
 *                               background refetches.
 *   - `mutations.retry: false` — surface errors immediately; the UI
 *                                decides whether to offer Retry.
 *
 * Created once at module scope so React 19 StrictMode's double-invocation
 * of effects does not produce two caches.
 */
import { QueryClient } from '@tanstack/react-query';

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      retry: 3,
      refetchOnWindowFocus: false,
    },
    mutations: {
      retry: false,
    },
  },
});
