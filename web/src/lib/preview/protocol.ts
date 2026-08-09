export type ChatFileRefV1 = {
  schemaVersion: 1;
  scope: 'chat';
  chatId: string;
  path: `/${'data' | 'memory' | 'logs'}/${string}`;
};

export type MountFileRefV1 = {
  schemaVersion: 1;
  scope: 'mount';
  path: `/mount/${string}`;
};

export type RunFileRefV1 = {
  schemaVersion: 1;
  scope: 'run';
  runId: string;
  path: `/run/${string}`;
};

export type FileRefV1 = ChatFileRefV1 | MountFileRefV1 | RunFileRefV1;

export type DiagramDraftPreviewRefV1 = {
  schemaVersion: 1;
  kind: 'diagram_draft';
  draftId: string;
  chatId: string;
  targetPath: `/data/${string}`;
  title: string;
};

export type PreviewResourceRefV1 =
  | { schemaVersion: 1; kind: 'file'; fileRef: FileRefV1 }
  | { schemaVersion: 1; kind: 'interactive'; artifactId: string }
  | DiagramDraftPreviewRefV1
  | {
      schemaVersion: 1;
      kind: 'background_jobs';
      chatId: string;
      jobId?: string;
      deliveryBatchId?: string;
    }
  | {
      schemaVersion: 1;
      kind: 'execution_plan';
      planId: string;
      runId: string;
      revision?: number;
    }
  | { schemaVersion: 1; kind: 'workflow'; workflowId: string };

export type PreviewRendererId =
  | 'text'
  | 'markdown'
  | 'html'
  | 'pdf'
  | 'docx'
  | 'pptx'
  | 'spreadsheet'
  | 'image'
  | 'audio'
  | 'video'
  | 'diagram'
  | 'unsupported';

export interface DiagramIssueV1 {
  issue_id?: string;
  severity: 'error' | 'warning' | 'info';
  disposition?: 'blocking' | 'repairable' | 'render_cue' | 'accepted';
  stage: 'schema' | 'semantic' | 'compile' | 'visual';
  code: string;
  json_pointer: string;
  element_id?: string | null;
  element_ids?: string[];
  json_pointers?: string[];
  message: string;
  suggested_fix?: string | null;
  geometry?: Record<string, unknown>;
  cause?: Record<string, unknown>;
  suggested_operations?: Array<Record<string, unknown>>;
  auto_fixable?: boolean;
}

export interface DiagramSceneV1 {
  schemaVersion: 1;
  diagramId: string;
  title: string;
  family: string;
  diagramType: string;
  compilerVersion: string;
  themeVersion: string;
  bounds: { x: number; y: number; width: number; height: number };
  nodes: Array<{
    id: string;
    kind: string;
    label: string;
    labelLines: string[];
    description?: string | null;
    descriptionLines: string[];
    styleRole: string;
    importance: string;
    assetRef?: string | null;
    ports: Array<{
      id: string;
      label?: string | null;
      side: 'NORTH' | 'EAST' | 'SOUTH' | 'WEST';
      direction: 'in' | 'out' | 'inout';
      x: number;
      y: number;
      sourcePointer: string;
    }>;
    bounds: { x: number; y: number; width: number; height: number };
    sourcePointer: string;
    metadata: Record<string, unknown>;
  }>;
  edges: Array<{
    id: string;
    source: string;
    target: string;
    kind: string;
    label?: string | null;
    importance: string;
    points: Array<{ x: number; y: number }>;
    sourcePointer: string;
    crossings?: Array<{
      x: number;
      y: number;
      style: 'gap' | 'bridge' | 'bundle';
      overEdgeId?: string | null;
    }>;
  }>;
  groups: Array<{
    id: string;
    label: string;
    styleRole: string;
    nodeIds: string[];
    bounds: { x: number; y: number; width: number; height: number };
    sourcePointer: string;
  }>;
  issues: DiagramIssueV1[];
}

export interface PreviewErrorInfo {
  code: string;
  params: Record<string, string | number | boolean>;
}

export interface PreviewDescriptorV1 {
  schemaVersion: 1;
  fileRef: FileRefV1;
  name: string;
  sizeBytes: number;
  contentType: string;
  detectedType: string;
  revision: string;
  renderer: PreviewRendererId;
  loadPolicy: 'inline' | 'stream' | 'range' | 'manual' | 'unsupported';
  capabilities: {
    preview: boolean;
    edit: boolean;
    download: boolean;
  };
  content?: {
    inlineText?: string | null;
    url?: string | null;
    truncated: boolean;
    rangeSupported: boolean;
  } | null;
  text?: {
    encoding: 'utf-8';
    bom: boolean;
    newline: 'LF' | 'CRLF';
    mixedNewlines: boolean;
  } | null;
  diagram?: {
    status: 'valid' | 'invalid';
    scene?: DiagramSceneV1 | null;
    issues: DiagramIssueV1[];
    sourceHash?: string;
    draft?: {
      draftId: string;
      status: string;
      sequence: number;
      terminal: boolean;
      operation: string;
      elementIds: string[];
    };
  } | null;
  error?: PreviewErrorInfo | null;
}

export interface PreviewFileWriteOut {
  fileRef: FileRefV1;
  revision: string;
  sizeBytes: number;
  contentType: string;
}

export interface PreviewResourceSessionV1 {
  schemaVersion: 1;
  resourceMounts: Array<{
    pathPrefix: string;
    rootUrl: string;
  }>;
  baseUrl: string;
  expiresIn: number;
}

export function fileRefKey(fileRef: FileRefV1): string {
  switch (fileRef.scope) {
    case 'chat':
      return `chat:${fileRef.chatId}:${fileRef.path}`;
    case 'mount':
      return `mount:${fileRef.path}`;
    case 'run':
      return `run:${fileRef.runId}:${fileRef.path}`;
  }
}


export function fileRefFromAgentPath(
  path: string,
  options: { chatId?: string | null; runId?: string | null },
): FileRefV1 | null {
  if (
    (path.startsWith('/data/') || path.startsWith('/memory/') || path.startsWith('/logs/'))
    && options.chatId
  ) {
    return {
      schemaVersion: 1,
      scope: 'chat',
      chatId: options.chatId,
      path: path as ChatFileRefV1['path'],
    };
  }
  if (path.startsWith('/mount/')) {
    return {
      schemaVersion: 1,
      scope: 'mount',
      path: path as `/mount/${string}`,
    };
  }
  if (path.startsWith('/run/') && options.runId) {
    return {
      schemaVersion: 1,
      scope: 'run',
      runId: options.runId,
      path: path as `/run/${string}`,
    };
  }
  return null;
}

/**
 * Resolve a Markdown href emitted by an Agent back to its private workspace
 * path. These paths are VFS references, not browser routes: navigating to
 * `/data/report.pdf` would incorrectly leave the app (and also drop a dynamic
 * deployment prefix). HTTP(S), mail, anchors, and arbitrary relative links are
 * deliberately left to the browser.
 */
export function agentFilePathFromHref(href: string | undefined): string | null {
  if (!href) return null;
  const value = href.trim();
  if (!value.startsWith('/')) return null;

  // Query/fragment syntax belongs to the Markdown link, not the VFS path.
  const pathPart = value.split(/[?#]/, 1)[0];
  let decoded: string;
  try {
    decoded = decodeURIComponent(pathPart);
  } catch {
    return null;
  }
  if (
    !decoded.startsWith('/data/')
    && !decoded.startsWith('/memory/')
    && !decoded.startsWith('/logs/')
    && !decoded.startsWith('/mount/')
    && !decoded.startsWith('/run/')
  ) {
    return null;
  }
  // Keep Preview resolution inside the selected VFS root even when an Agent
  // emits a hand-written or percent-encoded path.
  if (decoded.split('/').some((segment) => segment === '.' || segment === '..')) {
    return null;
  }
  return decoded;
}
