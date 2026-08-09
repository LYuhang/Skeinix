import { useEffect, useMemo } from 'react';
import { ReactFlowProvider } from '@xyflow/react';
import { ArrowUpRight, CheckCircle2, Download, GitBranch, X } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import { Button } from '@/components/ui/button';
import { Canvas } from '@/pages/canvas/Canvas';
import { RightInspector } from '@/pages/canvas/inspector/RightInspector';
import { useWorkflow } from '@/lib/api/queries/workflow';
import { downloadFilename, serializeWorkflow } from '@/lib/workflow/io';
import { useUIStore } from '@/stores/ui';
import { useWorkflowEditStore } from '@/stores/workflow-edit';

export interface ChatWorkflowViewerProps {
  workflowId: string | null;
  onClose: () => void;
}

export function ChatWorkflowViewer({ workflowId, onClose }: ChatWorkflowViewerProps) {
  const { t } = useTranslation();
  const setDraft = useWorkflowEditStore((state) => state.setDraft);
  const setCanvasReadOnly = useUIStore((state) => state.setCanvasReadOnly);
  const setInspectorOpen = useUIStore((state) => state.setInspectorOpen);
  const requestInspectorTab = useUIStore((state) => state.requestInspectorTab);
  const requestCheck = useUIStore((state) => state.requestCheck);
  const query = useWorkflow(workflowId ?? '');

  useEffect(() => {
    if (!workflowId) return;
    const intervalId = window.setInterval(() => void query.refetch(), 3000);
    return () => window.clearInterval(intervalId);
  }, [workflowId, query]);

  const meta = query.data?.meta;
  const workflow = query.data?.workflow;
  const displayWorkflow = useMemo(() => {
    if (!workflowId || !workflow) return null;
    return structuredClone(workflow) as Record<string, unknown>;
  }, [workflow, workflowId]);

  useEffect(() => {
    if (!workflowId) return;
    setCanvasReadOnly(true);
    setInspectorOpen(true);
    requestInspectorTab('auto', 'node');
    return () => setCanvasReadOnly(false);
  }, [workflowId, requestInspectorTab, setCanvasReadOnly, setInspectorOpen]);

  useEffect(() => {
    if (workflowId && displayWorkflow) setDraft(displayWorkflow);
  }, [displayWorkflow, workflowId, setDraft]);

  const downloadWorkflow = () => {
    if (!displayWorkflow) return;
    const blob = new Blob([serializeWorkflow(displayWorkflow)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    const version = meta?.active_v != null && meta?.active_sv != null
      ? `v${meta.active_v}.sv${meta.active_sv}`
      : null;
    anchor.href = url;
    anchor.download = downloadFilename(String(meta?.workflow_name || ''), version);
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);
  };

  const openWorkflowEditor = () => {
    if (!workflowId) return;
    onClose();
    const url = new URL(`workflow/${encodeURIComponent(workflowId)}`, window.location.href);
    window.open(url.toString(), '_blank', 'noopener,noreferrer');
  };

  if (!workflowId) return null;

  return (
    <aside
      className="pointer-events-auto flex h-full w-full min-w-0 flex-col bg-surface-work"
      data-role="chat-workflow-viewer"
    >
      <div className="chat-workflow-toolbar chat-pane-subheader flex h-10 shrink-0 items-center justify-between gap-2 px-3">
        <div className="min-w-0 flex-1" title={String(meta?.workflow_name || workflowId)}>
          <div className="flex min-w-0 items-center gap-2 text-sm font-medium">
            <GitBranch className="h-4 w-4 shrink-0 text-muted-foreground" />
            <span className="truncate">{String(meta?.workflow_name || workflowId)}</span>
            {meta?.active_v != null && meta?.active_sv != null && (
              <span className="shrink-0 text-xs font-normal text-muted-foreground">
                v{meta.active_v}.sv{meta.active_sv}
              </span>
            )}
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="h-8 px-2"
            disabled={!displayWorkflow}
            onClick={() => requestCheck()}
            aria-label={t('check', 'Check')}
            title={t('check', 'Check')}
          >
            <CheckCircle2 className="h-4 w-4" />
            <span className="chat-workflow-action-label">{t('check', 'Check')}</span>
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="h-8 px-2"
            disabled={!displayWorkflow}
            onClick={downloadWorkflow}
            aria-label={t('io.download', 'Download JSON')}
            title={t('io.download', 'Download JSON')}
          >
            <Download className="h-4 w-4" />
            <span className="chat-workflow-action-label">{t('io.download', 'Download JSON')}</span>
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="h-8 px-2"
            onClick={openWorkflowEditor}
            aria-label={t('chat.workflowViewer.openEditor', 'Open editor')}
            title={t('chat.workflowViewer.openEditor', 'Open editor')}
          >
            <ArrowUpRight className="h-4 w-4" />
            <span className="chat-workflow-action-label">
              {t('chat.workflowViewer.openEditor', 'Open editor')}
            </span>
          </Button>
          <Button
            variant="ghost"
            size="icon"
            aria-label={t('chat.workflowViewer.close', 'Close workflow viewer')}
            onClick={onClose}
          >
            <X className="h-4 w-4" />
          </Button>
        </div>
      </div>
      <div className="min-h-0 flex-1">
        {query.isLoading ? (
          <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
            {t('chat.workflowViewer.loading', 'Loading workflow...')}
          </div>
        ) : query.isError ? (
          <div className="flex h-full items-center justify-center text-sm text-destructive">
            {t('chat.workflowViewer.error', 'Failed to load workflow.')}
          </div>
        ) : (
          <ReactFlowProvider>
            <div className="flex h-full min-h-0">
              <div className="min-w-0 flex-1"><Canvas readOnly /></div>
              <RightInspector wfId={workflowId} readOnly variant="embedded" />
            </div>
          </ReactFlowProvider>
        )}
      </div>
    </aside>
  );
}
