import { getApiBase } from '@/lib/base-path';

const BASE = getApiBase();

export interface StorageItem {
  name: string;
  path: string;
  kind: 'file' | 'folder';
  size_bytes: number | null;
  modified_at: string | null;
  content_type: string | null;
  source: string | null;
  can_create_child: boolean;
  can_rename: boolean;
  can_delete: boolean;
  can_write: boolean;
}

export interface StorageListOut {
  path: string;
  items: StorageItem[];
  next_cursor: string | null;
  total_estimate: number | null;
  readonly: boolean;
}

export interface StorageReadOut {
  path: string;
  content_type: string;
  content: string | null;
  size_bytes: number;
  truncated: boolean;
}

export interface StorageWriteOut {
  path: string;
  size_bytes: number;
  content_type: string;
  replaced: boolean;
}

export interface StorageDeleteOut {
  deleted: number;
}

export interface StorageRenameOut {
  path: string;
}

async function authedFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const { useAuthStore } = await import('@/stores/auth');
  const token = useAuthStore.getState().token;
  const headers = new Headers(init.headers);
  if (token) headers.set('Authorization', `Bearer ${token}`);
  const resp = await fetch(`${BASE}${path}`, { ...init, headers });
  if (resp.status === 401) useAuthStore.getState().handle401();
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
      // non-JSON body
    }
    throw new Error(`${label} failed: ${resp.status} ${resp.statusText}${detail}`);
  }
  return (await resp.json()) as T;
}

function qs(params: Record<string, string | number | undefined | null>): string {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && `${v}` !== '') sp.set(k, `${v}`);
  }
  const s = sp.toString();
  return s ? `?${s}` : '';
}

export async function listStorage(args: {
  path: string;
  limit?: number;
  cursor?: string | null;
  search?: string;
  sort?: string;
}): Promise<StorageListOut> {
  const resp = await authedFetch(`/api/v1/storage/list${qs(args)}`);
  return jsonOrThrow<StorageListOut>(resp, 'listStorage');
}

export async function readStorage(path: string): Promise<StorageReadOut> {
  const resp = await authedFetch(`/api/v1/storage/content${qs({ path })}`);
  return jsonOrThrow<StorageReadOut>(resp, 'readStorage');
}

export async function downloadStorageBlob(path: string): Promise<Blob> {
  const resp = await authedFetch(`/api/v1/storage/raw${qs({ path })}`);
  if (!resp.ok) await jsonOrThrow<never>(resp, 'downloadStorageBlob');
  return await resp.blob();
}

export async function writeStorageContent(args: {
  path: string;
  content: string;
  content_type?: string;
}): Promise<StorageWriteOut> {
  const resp = await authedFetch('/api/v1/storage/content', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(args),
  });
  return jsonOrThrow<StorageWriteOut>(resp, 'writeStorageContent');
}

export async function uploadStorageFile(path: string, file: File): Promise<StorageWriteOut> {
  const fd = new FormData();
  fd.append('file', file);
  const resp = await authedFetch(`/api/v1/storage/upload${qs({ path })}`, {
    method: 'POST',
    body: fd,
  });
  return jsonOrThrow<StorageWriteOut>(resp, 'uploadStorageFile');
}

export async function mkdirStorage(path: string): Promise<StorageWriteOut> {
  const resp = await authedFetch('/api/v1/storage/mkdir', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path }),
  });
  return jsonOrThrow<StorageWriteOut>(resp, 'mkdirStorage');
}

export async function deleteStorage(path: string): Promise<StorageDeleteOut> {
  const resp = await authedFetch(`/api/v1/storage${qs({ path })}`, {
    method: 'DELETE',
  });
  return jsonOrThrow<StorageDeleteOut>(resp, 'deleteStorage');
}

export async function renameStorage(args: {
  old_path: string;
  new_path: string;
}): Promise<StorageRenameOut> {
  const resp = await authedFetch('/api/v1/storage/rename', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(args),
  });
  return jsonOrThrow<StorageRenameOut>(resp, 'renameStorage');
}
