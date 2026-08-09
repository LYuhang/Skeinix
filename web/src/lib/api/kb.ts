/**
 * Knowledge Base API client (Agent-native encrypted file retrieval).
 *
 * Thin typed wrappers over the 11 endpoints landed by T6 (`routes/kb.py`):
 *
 *   * `POST   /api/v1/kb`                              — create KB
 *   * `GET    /api/v1/kb`                              — list KBs (tenant-scoped via FORCE RLS)
 *   * `GET    /api/v1/kb/{id}`                         — KB detail (counts + latest_updated_at)
 *   * `PATCH  /api/v1/kb/{id}`                         — partial update (name, description)
 *   * `DELETE /api/v1/kb/{id}`                         — soft delete (204; 409 if any file indexing)
 *   * `GET    /api/v1/kb/{id}/files?status=...`        — list files (optional status filter alias)
 *   * `POST   /api/v1/kb/{id}/files`                   — upload (multipart/form-data)
 *   * `DELETE /api/v1/kb/{id}/files/{fid}`             — soft delete file (404 if missing — T6 fix #3)
 *   * `POST   /api/v1/kb/{id}/files/{fid}/reindex`     — re-enqueue parse + normalize
 *   * `POST   /api/v1/kb/search`                       — encrypted grep-style search
 *
 * Mirrors the hand-rolled-types approach in `mcp-servers.ts` /
 * `deployments.ts` — these routes were added after the last
 * `pnpm codegen:api:offline` snapshot, so a follow-up regen will eventually
 * fold them into the typed `apiClient`. Everything funnels through the same
 * per-file `authedFetch` shim (Bearer header + 401 → reopen login).
 *
 * T10 + T11 reuse the file + search + reindex functions; T9 only directly
 * calls `listKbs`, `createKb`, and `deleteKb`.
 */

// ---------------------------------------------------------------------------
// Contract — kept in sync with `routes/kb.py` + migration 007 schema.
// ---------------------------------------------------------------------------

export type KbFileStatus = 'pending' | 'indexing' | 'indexed' | 'failed';

export interface Kb {
  id: string;
  name: string;
  description: string | null;
  summary?: string | null;
  retrieval_strategy: 'agentic_lexical';
  created_at: string;
  updated_at: string;
  access: import('@/lib/api/organizations').ResourceAccess;
}

export interface KbDetail extends Kb {
  file_count: number;
  chunk_count: number;
  /**
   * `max(kb.updated_at, max(file.updated_at))` — drives frontend polling so
   * the detail panel can detect rename / file added / file indexed / file
   * deleted without polling every sub-resource. Restored by the T6 post-fix
   * (commit 663ef67) after an initial omission.
   */
  latest_updated_at: string;
}

export interface KbListItem extends Kb {
  file_count: number;
  chunk_count: number;
  pending_count: number;
  indexing_count: number;
  indexed_count: number;
  failed_count: number;
  latest_updated_at: string;
}

export interface KbFile {
  id: string;
  name: string;
  parser_type: string;
  file_size: number;
  status: KbFileStatus;
  error_message: string | null;
  chunk_count: number;
  created_at: string;
  access: import('@/lib/api/organizations').ResourceAccess;
}

export interface KbFileContentChunk {
  index: number;
  text: string;
}

export interface KbFileContent {
  file_id: string;
  file_name: string;
  parser_type: string;
  status: KbFileStatus;
  offset: number;
  next_offset: number;
  total_chunks: number;
  has_more: boolean;
  chunks: KbFileContentChunk[];
}

export interface KbSearchResult {
  chunk_id: string;
  file_id: string;
  file_name: string;
  kb_id: string;
  text: string;
  score: number;
  match_kind: 'exact_phrase' | 'all_terms' | 'partial_terms';
  matched_terms: string[];
  chunk_metadata: Record<string, unknown>;
}

export interface KbSearchResponse {
  results: KbSearchResult[];
}

export interface CreateKbBody {
  name: string;
  description?: string;
}

export interface UpdateKbBody {
  name?: string;
  description?: string;
}

export interface ListKbFilesParams {
  /**
   * Optional server-side filter restored by the T6 post-fix (commit 663ef67).
   * Accepts any `KbFileStatus` value.
   */
  status?: KbFileStatus;
}

export interface UploadKbFileResponse {
  file_id: string;
  task_id: string;
  status: string;
}

export interface ReindexKbFileResponse {
  file_id: string;
  task_id: string;
  status: string;
}

export interface SearchKbBody {
  kb_ids: string[];
  query: string;
  top_k?: number;
}

// ---------------------------------------------------------------------------
// Internal: same `authedFetch` shim as `mcp-servers.ts` / `deployments.ts`.
// Kept file-local on purpose — see the comment in `mcp-servers.ts`. T6 mounts
// the KB router follows the same `/api/v1` namespace as other business APIs.
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
  // Only set JSON Content-Type for non-FormData bodies. FormData must let
  // the browser set its own `multipart/form-data; boundary=...` header.
  if (init.body && !(init.body instanceof FormData) && !headers.has('Content-Type')) {
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
        detail = ` — ${JSON.stringify((j as { detail: unknown }).detail)}`;
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

async function okOrThrow(resp: Response, label: string): Promise<void> {
  if (!resp.ok) {
    let detail = '';
    try {
      const j = await resp.json();
      if (j && typeof j === 'object' && 'detail' in j) {
        detail = ` — ${JSON.stringify((j as { detail: unknown }).detail)}`;
      }
    } catch {
      // ignore non-JSON bodies
    }
    throw new Error(
      `${label} failed: ${resp.status} ${resp.statusText}${detail}`,
    );
  }
}

// ---------------------------------------------------------------------------
// Public API.
// ---------------------------------------------------------------------------

export async function listKbs(): Promise<KbListItem[]> {
  const resp = await authedFetch('/api/v1/kb');
  return jsonOrThrow<KbListItem[]>(resp, 'listKbs');
}

export async function createKb(body: CreateKbBody): Promise<Kb> {
  const resp = await authedFetch('/api/v1/kb', {
    method: 'POST',
    body: JSON.stringify(body),
  });
  return jsonOrThrow<Kb>(resp, 'createKb');
}

export async function getKb(id: string): Promise<KbDetail> {
  const resp = await authedFetch(`/api/v1/kb/${id}`);
  return jsonOrThrow<KbDetail>(resp, 'getKb');
}

export async function updateKb(id: string, body: UpdateKbBody): Promise<Kb> {
  const resp = await authedFetch(`/api/v1/kb/${id}`, {
    method: 'PATCH',
    body: JSON.stringify(body),
  });
  return jsonOrThrow<Kb>(resp, 'updateKb');
}

export async function deleteKb(id: string): Promise<void> {
  const resp = await authedFetch(`/api/v1/kb/${id}`, { method: 'DELETE' });
  await okOrThrow(resp, 'deleteKb');
}

export async function listKbFiles(
  kbId: string,
  params?: ListKbFilesParams,
): Promise<KbFile[]> {
  const qs = params?.status
    ? `?${new URLSearchParams({ status: params.status }).toString()}`
    : '';
  const resp = await authedFetch(`/api/v1/kb/${kbId}/files${qs}`);
  return jsonOrThrow<KbFile[]>(resp, 'listKbFiles');
}

export async function getKbFileContent(
  kbId: string,
  fileId: string,
  offset = 0,
  limit = 50,
): Promise<KbFileContent> {
  const qs = new URLSearchParams({
    offset: String(offset),
    limit: String(limit),
  });
  const resp = await authedFetch(
    `/api/v1/kb/${kbId}/files/${fileId}/content?${qs.toString()}`,
  );
  return jsonOrThrow<KbFileContent>(resp, 'getKbFileContent');
}

export async function uploadKbFile(
  kbId: string,
  file: File,
): Promise<UploadKbFileResponse> {
  const fd = new FormData();
  fd.append('file', file);
  const resp = await authedFetch(`/api/v1/kb/${kbId}/files`, {
    method: 'POST',
    body: fd,
  });
  return jsonOrThrow<UploadKbFileResponse>(resp, 'uploadKbFile');
}

export async function deleteKbFile(kbId: string, fileId: string): Promise<void> {
  const resp = await authedFetch(`/api/v1/kb/${kbId}/files/${fileId}`, {
    method: 'DELETE',
  });
  await okOrThrow(resp, 'deleteKbFile');
}

export async function reindexKbFile(
  kbId: string,
  fileId: string,
): Promise<ReindexKbFileResponse> {
  const resp = await authedFetch(`/api/v1/kb/${kbId}/files/${fileId}/reindex`, {
    method: 'POST',
  });
  return jsonOrThrow<ReindexKbFileResponse>(resp, 'reindexKbFile');
}

export async function searchKb(body: SearchKbBody): Promise<KbSearchResponse> {
  const resp = await authedFetch('/api/v1/kb/search', {
    method: 'POST',
    body: JSON.stringify(body),
  });
  return jsonOrThrow<KbSearchResponse>(resp, 'searchKb');
}
