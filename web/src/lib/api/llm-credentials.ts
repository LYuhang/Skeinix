/**
 * LLM credentials API client for the API Management Center.
 *
 * Thin typed wrappers over the 5 endpoints:
 *
 *   * `POST   /api/v1/llm-credentials`               — create   → OwnerOut
 *   * `GET    /api/v1/llm-credentials`               — list      → PublicOut[]
 *   * `GET    /api/v1/llm-credentials/{id}`          — owner view→ OwnerOut
 *   * `PUT    /api/v1/llm-credentials/{id}`          — update    → OwnerOut
 *   * `DELETE /api/v1/llm-credentials/{id}`          — soft delete (204)
 *
 * Mirrors the hand-rolled-types approach in `mcp-servers.ts` — these routes were
 * added after the last codegen snapshot, so everything funnels through the same
 * per-file `authedFetch` shim (Bearer header + 401 → reopen login).
 *
 * SECURITY: plaintext keys are write-only and never returned by the API. The
 * list surface (`CredentialPublic`) carries id/name/description/provider —
 * NEVER model_name/api_url/api_key. The owner view (`CredentialOwner`) adds
 * model_name/api_url + an `api_key_set` flag, still never the plaintext key.
 */

// ---------------------------------------------------------------------------
// Contract — kept in sync with `routes/llm_credentials.py` +
// `schemas/llm_credentials.py`.
// ---------------------------------------------------------------------------

import type { ResourceAccess } from '@/lib/api/organizations';

/** Public / list view — the surface PromptNode + agent pickers consume. */
export interface CredentialPublic {
  id: string;
  name: string;
  description: string | null;
  provider: string;
  runtime_scope: 'langchain' | 'codex';
  model_context_tokens: number | null;
  created_at: string;
  updated_at: string;
  access?: ResourceAccess | null;
}

/** Owner management / edit view. NO plaintext key — only `api_key_set`. */
export interface CredentialOwner {
  id: string;
  name: string;
  description: string | null;
  provider: string;
  runtime_scope: 'langchain' | 'codex';
  model_name: string;
  model_context_tokens: number | null;
  api_url: string | null;
  /** Optional outbound proxy (owner-only — may carry `user:pass@host`). */
  proxy: string | null;
  api_key_set: boolean;
  created_at: string;
  updated_at: string;
  access?: ResourceAccess | null;
}

export interface CreateCredentialBody {
  name: string;
  description?: string | null;
  provider: string;
  /** Runtime catalog that owns this credential. Defaults to LangChain. */
  runtime_scope?: 'langchain' | 'codex';
  model_name: string;
  model_context_tokens?: number | null;
  api_url?: string | null;
  /** Optional outbound proxy, e.g. `http://host:port`. */
  proxy?: string | null;
  api_key: string;
}

export interface UpdateCredentialBody {
  name?: string;
  description?: string | null;
  provider?: string;
  runtime_scope?: 'langchain' | 'codex';
  model_name?: string;
  model_context_tokens?: number | null;
  api_url?: string | null;
  /** Optional outbound proxy, e.g. `http://host:port`. */
  proxy?: string | null;
  /** Omit or send empty to KEEP the existing key. */
  api_key?: string;
}

// ---------------------------------------------------------------------------
// Internal: same `authedFetch` shim as `mcp-servers.ts`.
// ---------------------------------------------------------------------------

import { getApiBase } from '@/lib/base-path';

const BASE = getApiBase();

async function authedFetch(
  path: string,
  init: RequestInit = {},
): Promise<Response> {
  const { useAuthStore } = await import('@/stores/auth');
  const token = useAuthStore.getState().token;
  const headers = new Headers(init.headers);
  if (token) headers.set('Authorization', `Bearer ${token}`);
  if (init.body && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }
  const resp = await fetch(`${BASE}${path}`, { ...init, headers });
  if (resp.status === 401) {
    useAuthStore.getState().handle401();
  }
  return resp;
}

async function jsonOrThrow<T>(resp: Response, label: string): Promise<T> {
  if (!resp.ok) {
    let detail = '';
    try {
      const j = await resp.json();
      if (j && typeof j === 'object' && 'detail' in j) {
        detail = ` — ${String((j as { detail: unknown }).detail)}`;
      }
    } catch {
      // ignore non-JSON bodies
    }
    throw new Error(
      `${label} failed: ${resp.status} ${resp.statusText}${detail}`,
    );
  }
  return (await resp.json()) as T;
}

// ---------------------------------------------------------------------------
// Public API.
// ---------------------------------------------------------------------------

export async function listLlmCredentials(): Promise<CredentialPublic[]> {
  const resp = await authedFetch('/api/v1/llm-credentials');
  return jsonOrThrow<CredentialPublic[]>(resp, 'listLlmCredentials');
}

export async function getLlmCredential(id: string): Promise<CredentialOwner> {
  const resp = await authedFetch(`/api/v1/llm-credentials/${id}`);
  return jsonOrThrow<CredentialOwner>(resp, 'getLlmCredential');
}

export async function createLlmCredential(
  body: CreateCredentialBody,
): Promise<CredentialOwner> {
  const resp = await authedFetch('/api/v1/llm-credentials', {
    method: 'POST',
    body: JSON.stringify(body),
  });
  return jsonOrThrow<CredentialOwner>(resp, 'createLlmCredential');
}

export async function updateLlmCredential(
  id: string,
  body: UpdateCredentialBody,
): Promise<CredentialOwner> {
  const resp = await authedFetch(`/api/v1/llm-credentials/${id}`, {
    method: 'PUT',
    body: JSON.stringify(body),
  });
  return jsonOrThrow<CredentialOwner>(resp, 'updateLlmCredential');
}

export async function deleteLlmCredential(id: string): Promise<void> {
  const resp = await authedFetch(`/api/v1/llm-credentials/${id}`, {
    method: 'DELETE',
  });
  if (!resp.ok) {
    throw new Error(
      `deleteLlmCredential failed: ${resp.status} ${resp.statusText}`,
    );
  }
}
