import { render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { DiagramDraftPreviewRefV1, DiagramSceneV1 } from '@/lib/preview/protocol';
import { DiagramDraftPreview } from '../DiagramDraftPreview';

const previewApi = vi.hoisted(() => ({
  getDiagramDraftRenderRevisions: vi.fn(),
}));

vi.mock('@/lib/api/previews', async (importOriginal) => ({
  ...await importOriginal<typeof import('@/lib/api/previews')>(),
  getDiagramDraftRenderRevisions: previewApi.getDiagramDraftRenderRevisions,
}));

vi.mock('../DiagramPreviewRenderer', () => ({
  DiagramPreviewRenderer: ({ descriptor }: {
    descriptor: { revision: string; diagram?: { draft?: { status: string } } };
  }) => (
    <div data-testid="draft-renderer">
      {descriptor.revision}:{descriptor.diagram?.draft?.status}
    </div>
  ),
}));

const resource: DiagramDraftPreviewRefV1 = {
  schemaVersion: 1,
  kind: 'diagram_draft',
  draftId: 'draft-1',
  chatId: 'chat-1',
  targetPath: '/data/diagrams/system.vdiagram.json',
  title: 'System architecture',
};

const scene: DiagramSceneV1 = {
  schemaVersion: 1,
  diagramId: 'system',
  title: 'System architecture',
  family: 'architecture',
  diagramType: 'system-container',
  compilerVersion: '1.2.0',
  themeVersion: '1.0.0',
  bounds: { x: 0, y: 0, width: 400, height: 240 },
  nodes: [],
  edges: [],
  groups: [],
  issues: [],
};

function page(status: 'ready' | 'invalid' = 'ready') {
  return {
    draft_id: 'draft-1',
    chat_id: 'chat-1',
    turn_id: 'turn-1',
    status,
    items: [{
      revision_id: 'revision-1',
      sequence: 1,
      operation: 'create_base',
      element_ids: ['service'],
      scene_ref: 'scene://sha256:one',
      scene_hash: 'sha256:one',
      scene,
      created_at: new Date().toISOString(),
    }],
    latest_source_sequence: status === 'invalid' ? 2 : 1,
    latest_ready_sequence: 1,
    latest_ready_scene_ref: 'scene://sha256:one',
    pending_sequences: [],
    terminal: false,
    reset_to_latest: false,
  } as const;
}

beforeEach(() => {
  previewApi.getDiagramDraftRenderRevisions.mockReset();
  Object.defineProperty(document, 'visibilityState', {
    configurable: true,
    value: 'visible',
  });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('DiagramDraftPreview', () => {
  it('advances by cursor and ETag, then keeps the last valid Scene on failure', async () => {
    previewApi.getDiagramDraftRenderRevisions
      .mockResolvedValueOnce({ page: page(), etag: '"draft-v1"' })
      .mockResolvedValueOnce({ page: null, etag: '"draft-v1"' })
      .mockRejectedValueOnce(new Error('temporary disconnect'))
      .mockResolvedValue({ page: null, etag: '"draft-v1"' });

    render(<DiagramDraftPreview resource={resource} />);

    expect(await screen.findByTestId('draft-renderer')).toHaveTextContent(
      'revision-1:ready',
    );
    await waitFor(() => {
      expect(previewApi.getDiagramDraftRenderRevisions).toHaveBeenCalledTimes(2);
    }, { timeout: 1_500 });
    expect(previewApi.getDiagramDraftRenderRevisions).toHaveBeenNthCalledWith(
      2,
      expect.objectContaining({ after: 1, etag: '"draft-v1"' }),
    );
    await waitFor(() => {
      expect(screen.getByRole('status')).toHaveTextContent(/Keeping the last valid revision/);
    }, { timeout: 2_000 });
    expect(screen.getByTestId('draft-renderer')).toHaveTextContent('revision-1:ready');
  });

  it('pauses while hidden and catches up immediately when visible', async () => {
    Object.defineProperty(document, 'visibilityState', {
      configurable: true,
      value: 'hidden',
    });
    previewApi.getDiagramDraftRenderRevisions.mockResolvedValue({
      page: { ...page(), terminal: true },
      etag: '"draft-v1"',
    });
    render(<DiagramDraftPreview resource={resource} />);

    await new Promise((resolve) => window.setTimeout(resolve, 30));
    expect(previewApi.getDiagramDraftRenderRevisions).not.toHaveBeenCalled();

    Object.defineProperty(document, 'visibilityState', {
      configurable: true,
      value: 'visible',
    });
    document.dispatchEvent(new Event('visibilitychange'));
    expect(await screen.findByTestId('draft-renderer')).toHaveTextContent(
      'revision-1:ready',
    );
    expect(previewApi.getDiagramDraftRenderRevisions).toHaveBeenCalledTimes(1);
  });
});
