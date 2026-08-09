/**
 * MCP Servers API client (Settings → MCP Servers, MCP SP1).
 *
 * Thin typed wrappers over the 7 endpoints under `/api/v1/mcp-servers`:
 *
 *   * `GET    /api/v1/mcp-servers`               — list    → { items: McpServer[] }
 *   * `GET    /api/v1/mcp-servers/{id}`          — get     → McpServer
 *   * `POST   /api/v1/mcp-servers`               — create  → McpServer
 *   * `POST   /api/v1/mcp-servers/test`          — dry-run → McpTestResult
 *   * `PATCH  /api/v1/mcp-servers/{id}`          — update  → McpServer
 *   * `DELETE /api/v1/mcp-servers/{id}`          — soft delete (204)
 *   * `POST   /api/v1/mcp-servers/{id}/refresh`  — re-handshake → McpServer
 *
 * Mirrors `llm-credentials.ts` exactly — same per-file `authedFetch` shim
 * (Bearer header + 401 → reopen login) and `jsonOrThrow` error handling.
 *
 * SECURITY: the backend `_scrub` replaces a stored bearer `token` with `"***"`
 * on the way out — the plaintext is never re-leaked through any of these reads.
 */

// ---------------------------------------------------------------------------
// Contract — kept in sync with `routes/mcp_servers.py` (`_scrub` response).
// ---------------------------------------------------------------------------

import type { ResourceAccess } from '@/lib/api/organizations';

/** A registered MCP server row (token scrubbed to `"***"`). */
export interface McpServer {
  id: string;
  name: string;
  tool_prefix: string;
  transport: 'stdio' | 'sse' | 'streamable_http' | 'streamable-http' | 'http';
  endpoint: string;
  source?: McpCatalogSource | null;
  source_id?: string | null;
  source_url?: string | null;
  auth_mode: 'none' | 'configuration' | 'connection_discovery' | 'oauth';
  auth_metadata_url?: string | null;
  connection_status:
    | 'not_required'
    | 'connection_required'
    | 'connecting'
    | 'connected'
    | 'reconnect_required'
    | 'connection_failed';
  description?: string;
  description_source?:
    | 'registry'
    | 'server_metadata'
    | 'synthesized'
    | 'user_edited'
    | 'ai_generated'
    | 'fallback';
  description_model_id?: string | null;
  description_generated_at?: string | null;
  description_basis_hash?: string | null;
  auth_config: { type?: string; token?: string } | null;
  connection_config?: Record<string, unknown> | null;
  enabled: boolean;
  last_handshake_status: string | null;
  last_tool_count: number | null;
  last_tool_names: {
    name: string;
    description: string;
    input_schema?: unknown;
  }[] | null;
  last_handshake_at: string | null;
  created_at: string;
  updated_at: string;
  access?: ResourceAccess | null;
}

/** Body for create + test (same input shape). */
export interface McpServerInput {
  name: string;
  tool_prefix: string;
  transport: 'stdio' | 'sse' | 'streamable_http' | 'streamable-http' | 'http';
  endpoint: string;
  description?: string | null;
  connection_config?: Record<string, unknown> | null;
  auth_config?: { type: string; token?: string | null } | null;
}

/** Result of the dry-run handshake probe. */
export interface McpTestResult {
  status: string;
  tool_count: number | null;
  tool_names: {
    name: string;
    description: string;
    input_schema?: unknown;
  }[] | null;
}

export type McpCatalogSource = 'official' | 'smithery';

export interface McpCatalogConfigField {
  key: string;
  label: string;
  description: string;
  required: boolean;
  secret: boolean;
  target: string;
  input_type: 'string' | 'number' | 'boolean' | 'filepath';
  choices: string[];
  default: string | number | boolean | null;
  placeholder: string;
}

export interface McpCatalogItem {
  source: McpCatalogSource;
  source_id: string;
  name: string;
  description: string;
  version: string | null;
  verified: boolean;
  usage_count: number | null;
  homepage: string | null;
  published_at: string | null;
  connection: {
    transport: McpServerInput['transport'];
    endpoint: string;
    connection_config: Record<string, unknown>;
  } | null;
  config_fields: McpCatalogConfigField[];
  configuration_source: 'official_registry' | 'smithery_schema';
  auth_mode: 'none' | 'configuration' | 'connection_discovery' | 'oauth';
  auth_metadata_url?: string | null;
}

export interface McpCatalogResult {
  source: McpCatalogSource;
  ranking: 'browse' | 'popular' | 'search';
  items: McpCatalogItem[];
  has_more: boolean;
}

export interface PlatformMcpTool {
  name: string;
  description: string;
  input_schema: {
    type: 'object';
    properties: Record<string, unknown>;
    required?: string[];
    additionalProperties: boolean;
  };
  annotations: {
    readOnlyHint?: boolean;
    destructiveHint?: boolean;
    idempotentHint?: boolean;
    openWorldHint?: boolean;
  };
}

export interface PlatformMcpService {
  id: string;
  name: string;
  description: string;
  activation: string;
  activation_mode: 'base' | 'command';
  runtime_types: Array<'langchain' | 'codex'>;
  tools: PlatformMcpTool[];
}

// ---------------------------------------------------------------------------
// Internal: same `authedFetch` shim as `llm-credentials.ts`.
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
        detail = ` — ${formatErrorDetail((j as { detail: unknown }).detail)}`;
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

function formatErrorDetail(detail: unknown): string {
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((item) => {
        if (item && typeof item === 'object') {
          const obj = item as { msg?: unknown; loc?: unknown };
          const loc = Array.isArray(obj.loc) ? obj.loc.join('.') : '';
          const msg = typeof obj.msg === 'string' ? obj.msg : JSON.stringify(item);
          return loc ? `${loc}: ${msg}` : msg;
        }
        return String(item);
      })
      .join('; ');
  }
  try {
    return JSON.stringify(detail);
  } catch {
    return String(detail);
  }
}

// ---------------------------------------------------------------------------
// Public API.
// ---------------------------------------------------------------------------

export async function listMcpServers(): Promise<McpServer[]> {
  const resp = await authedFetch('/api/v1/mcp-servers');
  const body = await jsonOrThrow<{ items: McpServer[] }>(
    resp,
    'listMcpServers',
  );
  return body.items;
}

export async function listPlatformMcpServices(): Promise<PlatformMcpService[]> {
  const resp = await authedFetch('/api/v1/mcp-servers/platform');
  const body = await jsonOrThrow<{ items: PlatformMcpService[] }>(
    resp,
    'listPlatformMcpServices',
  );
  return body.items;
}

export async function searchMcpCatalog(
  source: McpCatalogSource,
  search = '',
  limit = 20,
): Promise<McpCatalogResult> {
  const params = new URLSearchParams({ source, search, limit: String(limit) });
  const resp = await authedFetch(`/api/v1/mcp-servers/catalog?${params}`);
  return jsonOrThrow<McpCatalogResult>(resp, 'searchMcpCatalog');
}

export async function resolveMcpCatalogItem(
  source: McpCatalogSource,
  sourceId: string,
): Promise<McpCatalogItem> {
  const params = new URLSearchParams({ source, source_id: sourceId });
  const resp = await authedFetch(`/api/v1/mcp-servers/catalog/resolve?${params}`);
  return jsonOrThrow<McpCatalogItem>(resp, 'resolveMcpCatalogItem');
}

export async function getMcpServer(id: string): Promise<McpServer> {
  const resp = await authedFetch(`/api/v1/mcp-servers/${id}`);
  return jsonOrThrow<McpServer>(resp, 'getMcpServer');
}

export async function createMcpServer(
  body: McpServerInput,
): Promise<McpServer> {
  const resp = await authedFetch('/api/v1/mcp-servers', {
    method: 'POST',
    body: JSON.stringify(body),
  });
  return jsonOrThrow<McpServer>(resp, 'createMcpServer');
}

export async function installMcpCatalogItem(
  source: McpCatalogSource,
  sourceId: string,
): Promise<McpServer> {
  const resp = await authedFetch('/api/v1/mcp-servers/catalog/install', {
    method: 'POST',
    body: JSON.stringify({ source, source_id: sourceId }),
  });
  return jsonOrThrow<McpServer>(resp, 'installMcpCatalogItem');
}

export async function startMcpOAuth(id: string): Promise<{
  authorization_url: string;
  callback_origin: string;
}> {
  const resp = await authedFetch(`/api/v1/mcp-servers/${id}/oauth/start`, {
    method: 'POST',
    body: JSON.stringify({ return_origin: window.location.origin }),
  });
  return jsonOrThrow<{ authorization_url: string; callback_origin: string }>(resp, 'startMcpOAuth');
}

export async function disconnectMcpOAuth(id: string): Promise<void> {
  const resp = await authedFetch(`/api/v1/mcp-servers/${id}/oauth/disconnect`, {
    method: 'POST',
  });
  if (!resp.ok) {
    await jsonOrThrow(resp, 'disconnectMcpOAuth');
  }
}

/**
 * Dry-run handshake. The backend returns either
 * `{ok: true, tool_count, tool_names}` or `{ok: false, error}`; normalize both
 * into `McpTestResult` so callers see a single `{status, tool_count, tool_names}`
 * shape (status = 'ok' on success, the error string otherwise).
 */
export async function testMcpServer(
  body: McpServerInput,
): Promise<McpTestResult> {
  const resp = await authedFetch('/api/v1/mcp-servers/test', {
    method: 'POST',
    body: JSON.stringify(body),
  });
  const raw = await jsonOrThrow<{
    ok: boolean;
    tool_count?: number | null;
    tool_names?: { name: string; description: string }[] | null;
    error?: string;
  }>(resp, 'testMcpServer');
  return {
    status: raw.ok ? 'ok' : raw.error ?? 'error',
    tool_count: raw.tool_count ?? null,
    tool_names: raw.tool_names ?? null,
  };
}

export async function updateMcpServer(
  id: string,
  patch: Partial<McpServerInput> & {
    enabled?: boolean;
    description_source?: McpServer['description_source'];
    description_model_id?: string | null;
    description_basis_hash?: string | null;
  },
): Promise<McpServer> {
  const resp = await authedFetch(`/api/v1/mcp-servers/${id}`, {
    method: 'PATCH',
    body: JSON.stringify(patch),
  });
  return jsonOrThrow<McpServer>(resp, 'updateMcpServer');
}

export async function deleteMcpServer(id: string): Promise<void> {
  const resp = await authedFetch(`/api/v1/mcp-servers/${id}`, {
    method: 'DELETE',
  });
  if (!resp.ok) {
    throw new Error(
      `deleteMcpServer failed: ${resp.status} ${resp.statusText}`,
    );
  }
}

export async function refreshMcpServer(id: string): Promise<McpServer> {
  const resp = await authedFetch(`/api/v1/mcp-servers/${id}/refresh`, {
    method: 'POST',
  });
  return jsonOrThrow<McpServer>(resp, 'refreshMcpServer');
}
