/**
 * React-query hooks for the LLM Credentials store (API Management Center).
 *
 * Unblocks the next agents (management page + PromptNode picker):
 *
 *   - `useLlmCredentials()`         — list (PublicOut[]; the picker surface)
 *   - `useLlmCredential(id)`        — single owner view (edit form)
 *   - `useCreateLlmCredential()`    — create
 *   - `useUpdateLlmCredential()`    — update (api_key omitted => keep existing)
 *   - `useDeleteLlmCredential()`    — soft delete
 *
 * Mutations invalidate the list (and the touched single-row query) so the UI
 * reflects writes without a manual refetch. Mirrors the structure of the other
 * `queries/*` modules (vfs.ts / tasks.ts).
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import {
  createLlmCredential,
  deleteLlmCredential,
  getLlmCredential,
  listLlmCredentials,
  updateLlmCredential,
} from '@/lib/api/llm-credentials';
import type {
  CreateCredentialBody,
  UpdateCredentialBody,
} from '@/lib/api/llm-credentials';

const LIST_KEY = ['llm-credentials', 'list'] as const;
const itemKey = (id: string) => ['llm-credentials', 'item', id] as const;

/** Tenant-scoped credential list (PUBLIC projection — no secrets). */
export const useLlmCredentials = (opts?: { enabled?: boolean }) =>
  useQuery({
    queryKey: LIST_KEY,
    queryFn: () => listLlmCredentials(),
    enabled: opts?.enabled ?? true,
  });

/** Single credential owner view (edit form). Disabled while `id` is falsy. */
export const useLlmCredential = (id: string | undefined) =>
  useQuery({
    queryKey: itemKey(id ?? ''),
    queryFn: () => getLlmCredential(id as string),
    enabled: !!id,
  });

export const useCreateLlmCredential = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: CreateCredentialBody) => createLlmCredential(body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: LIST_KEY });
    },
  });
};

export const useUpdateLlmCredential = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: UpdateCredentialBody }) =>
      updateLlmCredential(id, body),
    onSuccess: (_data, vars) => {
      void qc.invalidateQueries({ queryKey: LIST_KEY });
      void qc.invalidateQueries({ queryKey: itemKey(vars.id) });
    },
  });
};

export const useDeleteLlmCredential = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => deleteLlmCredential(id),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: LIST_KEY });
    },
  });
};
