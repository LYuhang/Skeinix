import { useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { AsyncState } from '@/components/ui/async-state';
import {
  getDiagramDraftRenderRevisions,
  type DiagramDraftRenderRevision,
  type DiagramDraftRevisionPage,
} from '@/lib/api/previews';
import type {
  DiagramDraftPreviewRefV1,
  PreviewDescriptorV1,
} from '@/lib/preview/protocol';
import { recordDiagramPreviewTimeline } from '@/lib/preview/diagram-timeline';
import { DiagramPreviewRenderer } from './DiagramPreviewRenderer';

const ACTIVE_POLL_MS = 500;
const IDLE_POLL_MS = 1_500;
const MAX_BACKOFF_MS = 5_000;

export function DiagramDraftPreview({ resource }: { resource: DiagramDraftPreviewRefV1 }) {
  const { t } = useTranslation();
  const [page, setPage] = useState<DiagramDraftRevisionPage | null>(null);
  const [latest, setLatest] = useState<DiagramDraftRenderRevision | null>(null);
  const [error, setError] = useState<string | null>(null);
  const cursorRef = useRef(0);
  const etagRef = useRef<string | null>(null);
  const failuresRef = useRef(0);
  const lastChangeAtRef = useRef(0);

  useEffect(() => {
    lastChangeAtRef.current = Date.now();
    let disposed = false;
    let timer: number | null = null;
    let controller: AbortController | null = null;

    const schedule = (delay: number) => {
      if (disposed) return;
      timer = window.setTimeout(() => void poll(), delay);
    };

    const poll = async () => {
      if (disposed) return;
      if (document.visibilityState === 'hidden') return;
      controller = new AbortController();
      try {
        const result = await getDiagramDraftRenderRevisions({
          draftId: resource.draftId,
          after: cursorRef.current,
          etag: etagRef.current,
          signal: controller.signal,
        });
        if (disposed) return;
        etagRef.current = result.etag;
        failuresRef.current = 0;
        setError(null);
        if (result.page) {
          setPage(result.page);
          const incoming = result.page.items;
          if (incoming.length) {
            const next = incoming[incoming.length - 1];
            const changed = next.sequence > cursorRef.current || result.page.reset_to_latest;
            cursorRef.current = Math.max(cursorRef.current, next.sequence);
            setLatest(next);
            if (changed) {
              lastChangeAtRef.current = Date.now();
              const committedAt = Date.parse(next.created_at);
              if (Number.isFinite(committedAt)) {
                recordDiagramPreviewTimeline({
                  stage: 'T0',
                  path: resource.targetPath,
                  revision: next.revision_id,
                  timestamp: committedAt,
                });
              }
              recordDiagramPreviewTimeline({
                stage: 'T1',
                path: resource.targetPath,
                revision: next.revision_id,
                timestamp: Date.now(),
              });
            }
          }
          if (result.page.terminal) return;
        }
        const active = Date.now() - lastChangeAtRef.current < 5_000;
        schedule(active ? ACTIVE_POLL_MS : IDLE_POLL_MS);
      } catch (reason) {
        if (disposed || controller.signal.aborted) return;
        failuresRef.current += 1;
        setError(reason instanceof Error ? reason.message : 'Diagram draft update failed');
        schedule(Math.min(MAX_BACKOFF_MS, ACTIVE_POLL_MS * (2 ** failuresRef.current)));
      }
    };

    const onVisibility = () => {
      if (document.visibilityState !== 'visible' || disposed) return;
      if (timer !== null) window.clearTimeout(timer);
      schedule(0);
    };
    document.addEventListener('visibilitychange', onVisibility);
    schedule(0);
    return () => {
      disposed = true;
      document.removeEventListener('visibilitychange', onVisibility);
      if (timer !== null) window.clearTimeout(timer);
      controller?.abort();
    };
  }, [resource.draftId, resource.targetPath]);

  const descriptor = useMemo<PreviewDescriptorV1 | null>(() => {
    if (!latest) return null;
    return {
      schemaVersion: 1,
      fileRef: {
        schemaVersion: 1,
        scope: 'chat',
        chatId: resource.chatId,
        path: resource.targetPath,
      },
      name: resource.title,
      sizeBytes: 0,
      contentType: 'application/vnd.vibecanvas.diagram+json',
      detectedType: 'diagram',
      revision: latest.revision_id,
      renderer: 'diagram',
      loadPolicy: 'inline',
      capabilities: { preview: true, edit: false, download: false },
      content: null,
      diagram: {
        status: 'valid',
        scene: latest.scene,
        issues: latest.scene.issues ?? [],
        draft: {
          draftId: resource.draftId,
          status: page?.status ?? 'ready',
          sequence: latest.sequence,
          terminal: page?.terminal ?? false,
          operation: latest.operation,
          elementIds: latest.element_ids,
        },
      },
      error: null,
    };
  }, [latest, page?.status, page?.terminal, resource]);

  if (!descriptor) {
    return (
      <AsyncState
        kind={error ? 'error' : 'loading'}
        title={error
          ? t('preview.diagramDraft.errorTitle', 'Diagram preview is temporarily unavailable')
          : t('preview.diagramDraft.loading', 'Waiting for the first valid diagram revision…')}
        description={error ?? t(
          'preview.diagramDraft.loadingDescription',
          'Incomplete or invalid intermediate writes will not replace the canvas.',
        )}
        className="h-full rounded-none border-0"
      />
    );
  }

  return (
    <div className="relative h-full min-h-0">
      <DiagramPreviewRenderer
        descriptor={descriptor}
        loadAllowed
        onDirtyChange={() => undefined}
      />
      {error ? (
        <div
          role="status"
          className="absolute bottom-3 left-1/2 z-40 max-w-md -translate-x-1/2 rounded-md border border-state-warning/30 bg-surface-raised px-3 py-2 text-xs shadow-lg"
        >
          {t(
            'preview.diagramDraft.retrying',
            'Live updates were interrupted. Keeping the last valid revision and retrying…',
          )}
        </div>
      ) : null}
    </div>
  );
}
