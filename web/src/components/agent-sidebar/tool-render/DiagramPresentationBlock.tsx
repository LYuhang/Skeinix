import { CheckCircle2, Maximize2, Network } from 'lucide-react';
import { lazy, Suspense, useMemo } from 'react';
import { useTranslation } from 'react-i18next';

import { MessageAvatar } from '@/components/agent-sidebar/MessageItem';
import type { MergedToolCall } from '@/components/agent-sidebar/types';
import { AsyncState } from '@/components/ui/async-state';
import { Button } from '@/components/ui/button';
import { usePreviewDescriptor } from '@/lib/api/queries/previews';
import type { FileRefV1 } from '@/lib/preview/protocol';
import { cn } from '@/lib/utils';
import {
  diagramPreviewPathFromStandardResult,
  parseStandardToolResult,
} from './parseStandardToolResult';

const DiagramPreviewRenderer = lazy(() =>
  import('@/pages/chat/preview/DiagramPreviewRenderer').then((module) => ({
    default: module.DiagramPreviewRenderer,
  })),
);

export function DiagramPresentationBlock({
  call,
  chatId,
  showAvatar = true,
  compact = false,
  onOpenFilePreview,
}: {
  call: MergedToolCall;
  chatId: string;
  showAvatar?: boolean;
  compact?: boolean;
  onOpenFilePreview?: (path: string) => void;
}) {
  const { t } = useTranslation();
  const path = useMemo(
    () => diagramPreviewPathFromStandardResult(parseStandardToolResult(call.result)),
    [call.result],
  );
  const fileRef = useMemo<FileRefV1>(() => ({
    schemaVersion: 1,
    scope: 'chat',
    chatId,
    path: (path ?? '/data/diagrams/unavailable.vdiagram.json') as `/data/${string}`,
  }), [chatId, path]);
  const descriptorQuery = usePreviewDescriptor(fileRef);
  const descriptor = path ? descriptorQuery.data : null;
  const title = path
    ? path.split('/').pop()?.replace(/\.vdiagram\.json$/i, '') || t('preview.diagram.title', 'Diagram')
    : t('preview.diagram.title', 'Diagram');

  return (
    <div
      className="flex items-start justify-start gap-3"
      data-message-role="assistant"
      data-tool-name="present_diagram"
      data-role="diagram-presentation"
      data-testid="diagram-presentation"
    >
      {!compact && (showAvatar
        ? <MessageAvatar label="A" tone="agent" />
        : <div className="h-9 w-9 shrink-0" />)}
      <div className={cn('w-full min-w-0', compact ? 'max-w-[94%]' : 'max-w-[82%]')} data-message-content-rail="assistant">
        <div className="overflow-hidden rounded-xl border border-edge-structural bg-surface-raised">
          <div className="flex items-center gap-3 border-b border-edge-subtle bg-surface-sunken/45 px-3 py-2.5">
            <span className="grid size-8 shrink-0 place-items-center rounded-lg border border-edge-subtle bg-background/85 text-primary">
              <Network className="size-4" aria-hidden="true" />
            </span>
            <div className="min-w-0 flex-1">
              <div className="truncate text-sm font-semibold" title={title}>{title}</div>
              <div className="mt-0.5 flex items-center gap-1.5 text-xs text-muted-foreground">
                <CheckCircle2 className="size-3 text-state-success" aria-hidden="true" />
                {t('preview.diagram.presented', 'Diagram ready')}
              </div>
            </div>
            {!compact && path && onOpenFilePreview ? (
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="h-8 shrink-0 gap-1.5"
                data-action="diagram-open-preview"
                onClick={() => onOpenFilePreview(path)}
              >
                <Maximize2 className="size-3.5" aria-hidden="true" />
                <span className="hidden sm:inline">{t('tool.interactive.open_preview', 'Open in preview')}</span>
              </Button>
            ) : null}
          </div>
          <div className="h-56 bg-surface-work sm:h-64" aria-label={t('preview.diagram.inlinePreview', 'Diagram preview')}>
            {!path || descriptorQuery.isError ? (
              <AsyncState
                kind="error"
                title={t('preview.diagram.inlineUnavailable', 'Diagram preview is unavailable')}
                description={descriptorQuery.error?.message}
                className="h-full rounded-none border-0"
              />
            ) : descriptorQuery.isLoading || !descriptor ? (
              <AsyncState
                kind="loading"
                title={t('preview.resolving', 'Resolving file preview…')}
                className="h-full rounded-none border-0"
              />
            ) : descriptor.renderer !== 'diagram' ? (
              <AsyncState
                kind="error"
                title={t('preview.diagram.inlineUnavailable', 'Diagram preview is unavailable')}
                className="h-full rounded-none border-0"
              />
            ) : (
              <div
                className="diagram-inline-preview pointer-events-none h-full overflow-hidden [&_[data-action]]:hidden"
                aria-hidden="true"
              >
                <Suspense fallback={<AsyncState kind="loading" title={t('preview.resolving', 'Resolving file preview…')} className="h-full rounded-none border-0" />}>
                  <DiagramPreviewRenderer
                    descriptor={descriptor}
                    loadAllowed
                    onDirtyChange={() => undefined}
                  />
                </Suspense>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
