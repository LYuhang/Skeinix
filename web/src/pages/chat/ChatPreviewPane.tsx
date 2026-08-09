import { lazy, Suspense, useCallback, useRef, useState } from 'react';
import { ChevronDown, MessageSquare, PanelRightClose, X } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import {
  InteractiveArtifactPreview,
  type SubmitInteractiveAsNewTurn,
} from '@/components/agent-sidebar/tool-render/InteractiveArtifactBlock';
import { AsyncState } from '@/components/ui/async-state';
import { Button } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import type { ChatPreviewItem } from '@/lib/chat/preview-state';
import { cn } from '@/lib/utils';
import type { ChatFilePreviewHandle } from './preview/ChatFilePreview';

const ChatFilePreview = lazy(() =>
  import('./preview/ChatFilePreview').then((module) => ({ default: module.ChatFilePreview })),
);
const ChatWorkflowViewer = lazy(() =>
  import('./ChatWorkflowViewer').then((module) => ({ default: module.ChatWorkflowViewer })),
);
const BackgroundJobsPreview = lazy(() =>
  import('./preview/BackgroundJobsPreview').then((module) => ({ default: module.BackgroundJobsPreview })),
);
const ExecutionPlanPreview = lazy(() =>
  import('./preview/ExecutionPlanPreview').then((module) => ({ default: module.ExecutionPlanPreview })),
);
const DiagramDraftPreview = lazy(() =>
  import('./preview/DiagramDraftPreview').then((module) => ({ default: module.DiagramDraftPreview })),
);

export interface ChatPreviewPaneProps {
  scopeId: string;
  open: boolean;
  items: ChatPreviewItem[];
  resources: ChatPreviewItem[];
  activeId: string | null;
  onToggleOpen: (open: boolean) => void;
  onSelect: (id: string) => void;
  onOpenResource: (item: ChatPreviewItem) => void;
  onOpenInteractiveFile?: (path: string) => void;
  onCloseItem: (id: string) => void;
  onSubmitInteractiveAsNewMessage?: SubmitInteractiveAsNewTurn;
}

export function ChatPreviewPane({
  scopeId,
  open,
  items,
  resources,
  activeId,
  onToggleOpen,
  onSelect,
  onOpenResource,
  onOpenInteractiveFile,
  onCloseItem,
  onSubmitInteractiveAsNewMessage,
}: ChatPreviewPaneProps) {
  const { t } = useTranslation();
  const [resourcesOpen, setResourcesOpen] = useState(false);
  const active = items.find((item) => item.id === activeId) ?? items[0] ?? null;
  const fileViewerRef = useRef<ChatFilePreviewHandle>(null);
  const runWithActiveLeaveGuard = useCallback((action: () => void) => {
    if (active?.resource.kind === 'file' && fileViewerRef.current) {
      fileViewerRef.current.requestLeave(action);
      return;
    }
    action();
  }, [active]);

  if (!open) return null;

  const activeTitle = active?.title || t('chat.preview.emptyTitle', 'Preview');
  const activeTypeLabel = active?.resource.kind === 'workflow'
    ? t('chat.preview.type.workflow', 'Workflow')
    : active?.resource.kind === 'file'
      ? t('chat.preview.type.file', 'File')
      : active?.resource.kind === 'diagram_draft'
        ? t('chat.preview.type.diagramDraft', 'Diagram draft')
      : active?.resource.kind === 'interactive'
        ? t('chat.preview.type.interactive', 'Interactive')
        : active?.resource.kind === 'background_jobs'
          ? t('chat.preview.type.backgroundJobs', 'Background jobs')
          : active?.resource.kind === 'execution_plan'
            ? t('chat.preview.type.executionPlan', 'Execution plan')
        : t('chat.preview.type.empty', 'No resource');

  return (
    <aside
      className="chat-preview-pane flex h-full w-full min-w-0 flex-col bg-surface-work"
      data-role="chat-preview-pane"
      aria-label={t('chat.preview.paneLabel', 'Preview')}
    >
      <div className="chat-preview-header chat-pane-header flex h-11 shrink-0 items-center gap-2 px-3">
        <DropdownMenu open={resourcesOpen} onOpenChange={setResourcesOpen}>
          <DropdownMenuTrigger asChild>
            <button
              type="button"
              className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md border border-transparent text-muted-foreground transition-colors hover:bg-surface-hover hover:text-foreground"
              aria-label={t('chat.preview.resources', 'Preview resources')}
              title={t('chat.preview.resources', 'Preview resources')}
            >
              <ChevronDown className="h-4 w-4" />
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start" className="w-72">
            <DropdownMenuLabel>{t('chat.preview.resources', 'Preview resources')}</DropdownMenuLabel>
            <DropdownMenuSeparator />
            {resources.length === 0 ? (
              <DropdownMenuItem disabled>
                {t('chat.preview.noResources', 'No preview resources yet')}
              </DropdownMenuItem>
            ) : resources.map((item) => (
              <DropdownMenuItem
                key={item.id}
                className="min-w-0"
                onClick={() => {
                  if (item.id === active?.id) return;
                  runWithActiveLeaveGuard(() => onOpenResource(item));
                }}
              >
                <span className="min-w-0 flex-1 truncate">{item.title}</span>
                <span className="shrink-0 text-xs text-muted-foreground">{item.resource.kind}</span>
              </DropdownMenuItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>
        <div className="min-w-0 flex-1" title={activeTitle}>
          <button
            type="button"
            className="flex max-w-full items-center gap-2 text-left"
            onClick={() => setResourcesOpen(true)}
          >
            <span className="truncate text-sm font-medium">{activeTitle}</span>
            <span className="chat-preview-active-type shrink-0 rounded bg-surface-sunken px-1.5 py-0.5 text-xs leading-4 text-content-tertiary">
              {activeTypeLabel}
            </span>
          </button>
        </div>
        {active && (
          <Button
            variant="ghost"
            size="icon"
            className="toolbar-icon-button"
            aria-label={t('chat.preview.closeItem', 'Close preview item')}
            onClick={() => runWithActiveLeaveGuard(() => onCloseItem(active.id))}
          >
            <X className="h-4 w-4" />
          </Button>
        )}
        <Button
          variant="ghost"
          size="icon"
          className="toolbar-icon-button"
          aria-label={t('chat.preview.closePane', 'Close preview')}
          onClick={() => runWithActiveLeaveGuard(() => onToggleOpen(false))}
        >
          <PanelRightClose className="h-4 w-4" />
        </Button>
      </div>
      {items.length > 1 ? (
        <div className="chat-pane-subheader app-scrollbar flex h-9 shrink-0 gap-1 overflow-x-auto px-2 py-1">
          {items.map((item) => {
            const selected = active?.id === item.id;
            return (
              <div
                key={item.id}
                className={cn(
                  'group flex min-w-0 max-w-[180px] items-center gap-1.5 border-b-2 px-2 text-left text-xs transition-colors duration-feedback',
                  selected
                    ? 'border-focus bg-surface-work text-foreground'
                    : 'border-transparent text-muted-foreground hover:bg-muted/50 hover:text-foreground',
                )}
                title={item.title}
              >
                <button
                  type="button"
                  className="min-w-0 flex-1 truncate text-left"
                  onClick={() => {
                    if (item.id === active?.id) return;
                    runWithActiveLeaveGuard(() => onSelect(item.id));
                  }}
                >
                  {item.title}
                </button>
                <button
                  type="button"
                  className="rounded p-0.5 opacity-55 hover:bg-background hover:opacity-100"
                  aria-label={t('chat.preview.closeItem', 'Close preview item')}
                  onClick={(event) => {
                    event.stopPropagation();
                    if (item.id === active?.id) {
                      runWithActiveLeaveGuard(() => onCloseItem(item.id));
                    } else {
                      onCloseItem(item.id);
                    }
                  }}
                >
                  <X className="h-3 w-3" />
                </button>
              </div>
            );
          })}
        </div>
      ) : null}
      <div className="min-h-0 flex-1 bg-surface-work">
        {!active ? (
          <div className="flex h-full flex-col items-center justify-center gap-2 p-6 text-center text-sm text-muted-foreground">
            <MessageSquare className="h-6 w-6" />
            <div>{t('chat.preview.empty', 'Open a workflow, artifact, or sandbox file to preview it here.')}</div>
          </div>
        ) : active.resource.kind === 'workflow' ? (
          <Suspense fallback={<AsyncState kind="loading" title={t('chat.preview.loadingWorkflow', 'Loading workflow...')} />}>
            <ChatWorkflowViewer workflowId={active.resource.workflowId} onClose={() => onCloseItem(active.id)} />
          </Suspense>
        ) : active.resource.kind === 'file' ? (
          <Suspense fallback={<AsyncState kind="loading" title={t('chat.preview.loadingFile', 'Loading file...')} />}>
            <ChatFilePreview
              ref={fileViewerRef}
              fileRef={active.resource.fileRef}
              onOpenFile={onOpenInteractiveFile}
            />
          </Suspense>
        ) : active.resource.kind === 'diagram_draft' ? (
          <Suspense fallback={<AsyncState kind="loading" title={t('chat.preview.loadingDiagramDraft', 'Loading diagram draft…')} />}>
            <DiagramDraftPreview resource={active.resource} />
          </Suspense>
        ) : active.resource.kind === 'interactive' ? (
          <div className="flex h-full flex-col bg-surface-work">
            <div className="min-h-0 flex-1 overflow-auto p-4">
              <div className="overflow-hidden rounded-lg border border-edge-subtle bg-surface-raised">
                <InteractiveArtifactPreview
                  artifact={(active as Extract<ChatPreviewItem, { artifact: unknown }>).artifact}
                  maxHeight="calc(100vh - 12rem)"
                  onSubmitAsNewMessage={onSubmitInteractiveAsNewMessage}
                  onOpenFilePreview={onOpenInteractiveFile}
                />
              </div>
            </div>
          </div>
        ) : active.resource.kind === 'execution_plan' ? (
          <Suspense fallback={<AsyncState kind="loading" title="Loading execution plan…" />}>
            <ExecutionPlanPreview
              planId={active.resource.planId}
              runId={active.resource.runId}
              revision={active.resource.revision}
            />
          </Suspense>
        ) : (
          <Suspense fallback={<AsyncState kind="loading" title="Loading background tasks…" />}>
            <BackgroundJobsPreview
              scopeId={scopeId}
              chatId={active.resource.chatId}
              initialJobId={active.resource.jobId}
              deliveryBatchId={active.resource.deliveryBatchId}
              onOpenFile={onOpenInteractiveFile}
            />
          </Suspense>
        )}
      </div>
    </aside>
  );
}
