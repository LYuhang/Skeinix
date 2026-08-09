import { getApiBase } from '@/lib/base-path';
import type {
  FileRefV1,
  PreviewDescriptorV1,
  PreviewFileWriteOut,
  PreviewResourceSessionV1,
  DiagramSceneV1,
} from '@/lib/preview/protocol';
import { resolveApiUrl } from '@/lib/base-path';

const BASE = getApiBase();

export class PreviewApiError extends Error {
  readonly status: number;
  readonly detail: string;

  constructor(
    message: string,
    status: number,
    detail: string,
  ) {
    super(message);
    this.name = 'PreviewApiError';
    this.status = status;
    this.detail = detail;
  }
}

async function authedFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const { useAuthStore } = await import('@/stores/auth');
  const token = useAuthStore.getState().token;
  const headers = new Headers(init.headers);
  if (token) headers.set('Authorization', `Bearer ${token}`);
  const response = await fetch(`${BASE}${path}`, { ...init, headers });
  if (response.status === 401) useAuthStore.getState().handle401();
  return response;
}

async function jsonOrThrow<T>(response: Response, label: string): Promise<T> {
  if (response.ok) return response.json() as Promise<T>;
  let detail = response.statusText;
  try {
    const body = await response.json() as { detail?: unknown };
    if (typeof body.detail === 'string') detail = body.detail;
  } catch {
    // Keep the HTTP status text for non-JSON failures.
  }
  throw new PreviewApiError(`${label} failed: ${detail}`, response.status, detail);
}

export async function resolvePreview(
  fileRef: FileRefV1,
  signal?: AbortSignal,
): Promise<PreviewDescriptorV1> {
  const response = await authedFetch('/api/v1/previews/resolve', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ fileRef }),
    signal,
  });
  return jsonOrThrow<PreviewDescriptorV1>(response, 'resolvePreview');
}

export async function writePreviewFile(args: {
  fileRef: FileRefV1;
  expectedRevision: string;
  contentType: string;
  content: string;
}): Promise<PreviewFileWriteOut> {
  const response = await authedFetch('/api/v1/previews/file', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(args),
  });
  return jsonOrThrow<PreviewFileWriteOut>(response, 'writePreviewFile');
}

export async function createPreviewResourceSession(
  fileRef: FileRefV1,
  signal?: AbortSignal,
): Promise<PreviewResourceSessionV1> {
  const response = await authedFetch('/api/v1/previews/resource-session', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ fileRef }),
    signal,
  });
  const session = await jsonOrThrow<PreviewResourceSessionV1>(
    response,
    'createPreviewResourceSession',
  );
  return {
    ...session,
    resourceMounts: session.resourceMounts.map((mount) => ({
      ...mount,
      rootUrl: resolveApiUrl(mount.rootUrl),
    })),
    baseUrl: resolveApiUrl(session.baseUrl),
  };
}

export async function exportPreviewDiagram(args: {
  fileRef: FileRefV1;
  expectedRevision: string;
  format: 'svg' | 'png' | 'pdf';
  theme?: 'light';
  scale?: number;
  background?: 'white';
}): Promise<{ blob: Blob; filename: string }> {
  const response = await authedFetch('/api/v1/previews/diagram/export', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(args),
  });
  if (!response.ok) await jsonOrThrow(response, 'exportPreviewDiagram');
  const disposition = response.headers.get('Content-Disposition') ?? '';
  const match = disposition.match(/filename="([^"]+)"/i);
  return {
    blob: await response.blob(),
    filename: match?.[1] ?? `diagram.${args.format}`,
  };
}

export interface DiagramViewportBounds {
  x: number;
  y: number;
  width: number;
  height: number;
}

export async function updateActiveDiagramView(args: {
  chatId: string;
  path: `/data/${string}`;
  revision: string;
  sourceHash: string;
  selectedElementIds: string[];
  viewportBounds: DiagramViewportBounds | null;
}): Promise<void> {
  const response = await authedFetch(
    `/api/v1/chats/${encodeURIComponent(args.chatId)}/active-diagram/view`,
    {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        path: args.path,
        revision: args.revision,
        source_hash: args.sourceHash,
        selected_element_ids: args.selectedElementIds,
        viewport_bounds: args.viewportBounds,
      }),
    },
  );
  await jsonOrThrow(response, 'updateActiveDiagramView');
}

export interface DiagramDraftRenderRevision {
  revision_id: string;
  sequence: number;
  operation: string;
  element_ids: string[];
  scene_ref: string;
  scene_hash: string;
  scene: DiagramSceneV1;
  created_at: string;
}

export interface DiagramDraftRevisionPage {
  draft_id: string;
  chat_id: string;
  turn_id: string;
  status: 'writing' | 'parsing' | 'compiling' | 'ready' | 'invalid' | 'superseded' | 'committed' | 'cancelled';
  items: DiagramDraftRenderRevision[];
  latest_source_sequence: number;
  latest_ready_sequence: number;
  latest_ready_scene_ref: string | null;
  pending_sequences: number[];
  terminal: boolean;
  reset_to_latest: boolean;
}

export async function getDiagramDraftRenderRevisions(args: {
  draftId: string;
  after: number;
  etag?: string | null;
  signal?: AbortSignal;
}): Promise<{ page: DiagramDraftRevisionPage | null; etag: string | null }> {
  const response = await authedFetch(
    `/api/v1/previews/diagram-drafts/${encodeURIComponent(args.draftId)}`
      + `/render-revisions?after=${args.after}&limit=20`,
    {
      headers: args.etag ? { 'If-None-Match': args.etag } : undefined,
      signal: args.signal,
    },
  );
  if (response.status === 304) {
    return { page: null, etag: response.headers.get('ETag') ?? args.etag ?? null };
  }
  const page = await jsonOrThrow<DiagramDraftRevisionPage>(
    response,
    'getDiagramDraftRenderRevisions',
  );
  return { page, etag: response.headers.get('ETag') };
}
