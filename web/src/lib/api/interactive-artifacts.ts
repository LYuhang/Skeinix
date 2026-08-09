import { getApiBase, resolveApiUrl } from '@/lib/base-path';
import { useAuthStore } from '@/stores/auth';

const BASE = getApiBase();

export class InteractiveArtifactRequestError extends Error {
  readonly status: number;

  constructor(status: number, detail: string) {
    super(`interactive artifact request failed: ${status}: ${detail}`);
    this.name = 'InteractiveArtifactRequestError';
    this.status = status;
  }
}

async function authedJson<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = useAuthStore.getState().token;
  const headers = new Headers(init.headers);
  headers.set('Accept', 'application/json');
  if (init.body) headers.set('Content-Type', 'application/json');
  if (token) headers.set('Authorization', `Bearer ${token}`);
  const response = await fetch(`${BASE}${path}`, { ...init, headers });
  if (response.status === 401) useAuthStore.getState().handle401();
  if (!response.ok) {
    let detail = 'request failed';
    try {
      const payload = await response.json() as { detail?: unknown };
      if (typeof payload.detail === 'string' && payload.detail) detail = payload.detail;
    } catch {
      // Preserve the HTTP status when the response is not JSON.
    }
    throw new InteractiveArtifactRequestError(response.status, detail);
  }
  return await response.json() as T;
}

export interface InteractiveResourceSession {
  artifact_id: string;
  resource_mounts: InteractiveResourceMount[];
  base_url: string;
  expires_in: number;
  draft_debounce_ms: number;
}

export interface InteractiveResourceMount {
  path_prefix: string;
  root_url: string;
}

export async function createInteractiveResourceSession(
  artifactId: string,
): Promise<InteractiveResourceSession> {
  const result = await authedJson<InteractiveResourceSession>(
    `/api/v1/interactive-artifacts/${encodeURIComponent(artifactId)}/resource-session`,
    { method: 'POST' },
  );
  return {
    ...result,
    resource_mounts: result.resource_mounts
      .map((mount) => ({
        path_prefix: (mount.path_prefix.startsWith('/')
          ? mount.path_prefix
          : `/${mount.path_prefix}`).replace(/\/*$/, '/'),
        root_url: resolveApiUrl(mount.root_url),
      }))
      .sort((left, right) => right.path_prefix.length - left.path_prefix.length),
    base_url: resolveApiUrl(result.base_url),
  };
}

/** Resolve an Agent-visible Linux path using the backend-provided mount table. */
export function resolveInteractiveResourceUrl(
  path: string,
  session: Pick<InteractiveResourceSession, 'resource_mounts' | 'base_url'>,
): string {
  if (/^https?:\/\//i.test(path)) return path;
  if (path.startsWith('/')) {
    const mount = session.resource_mounts.find(({ path_prefix: prefix }) =>
      path.startsWith(prefix),
    );
    if (mount) {
      const rootUrl = mount.root_url.endsWith('/') ? mount.root_url : `${mount.root_url}/`;
      return `${rootUrl}${path.slice(mount.path_prefix.length).replace(/^\/+/, '')}`;
    }
  }
  return new URL(path, session.base_url).toString();
}

export async function saveInteractiveDraft(
  artifactId: string,
  state: Record<string, unknown>,
): Promise<{ status: 'saved' | 'frozen'; widget_state: Record<string, unknown> }> {
  return await authedJson(
    `/api/v1/interactive-artifacts/${encodeURIComponent(artifactId)}/state`,
    { method: 'PUT', body: JSON.stringify({ state }) },
  );
}

export interface InteractiveResultFile {
  path: string;
  result_path?: string;
  content_type: string;
  size_bytes: number;
  hash: string;
  revision?: string;
}

export async function writeInteractiveVfsFile(
  artifactId: string,
  {
    path,
    content,
    contentType = 'application/octet-stream',
  }: {
    path?: string;
    content: string;
    contentType?: string;
  },
): Promise<InteractiveResultFile> {
  return await authedJson(
    `/api/v1/interactive-artifacts/${encodeURIComponent(artifactId)}/result-file`,
    {
      method: 'PUT',
      body: JSON.stringify({
        path,
        content,
        content_type: contentType,
      }),
    },
  );
}
