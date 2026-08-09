/**
 * Enums query — `GET /api/v1/enums`.
 *
 * Backend returns `{ enums: { field_types, programming_languages,
 * model_names, workflow_domains, ... } }`. We unwrap one level so consumers
 * use `data?.field_types`, etc.
 *
 * The OpenAPI schema declares `enums` as an open `Record<string, unknown>`
 * (the legacy `enums.get_frontend_enums()` shape is dynamic), so callers
 * must narrow each list at use-site, e.g.
 *   `(enums?.field_types as string[] | undefined) ?? []`.
 *
 * `staleTime` is set to 10 min — these lists never change at runtime within
 * a session, and forcing a refetch on every editor mount would be wasteful.
 */
import { useQuery } from '@tanstack/react-query';
import { apiClient } from '@/lib/api/client';

export const useEnums = () =>
  useQuery({
    queryKey: ['enums'],
    queryFn: async () => {
      const { data, error } = await apiClient.GET('/api/v1/enums');
      if (error) throw error;
      return data.enums as Record<string, unknown>;
    },
    staleTime: 10 * 60_000,
  });

/**
 * Convenience: pull a string-list enum from the query result, falling back
 * to an empty array while the query is loading or if the key is missing.
 */
export const getEnumList = (
  enums: Record<string, unknown> | undefined,
  key: string,
): string[] => {
  const raw = enums?.[key];
  return Array.isArray(raw) ? (raw as string[]) : [];
};
