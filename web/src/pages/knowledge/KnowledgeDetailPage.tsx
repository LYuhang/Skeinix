import { useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  BookOpen,
  RefreshCw,
  Search,
  Share2,
  Trash2,
  Upload,
} from 'lucide-react';
import { useNavigate, useParams, useSearchParams } from 'react-router';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import { EntityDetailShell } from '@/components/layout/entity-detail-shell';
import { ResourceShareDialog } from '@/components/modals/ResourceShareDialog';
import { AsyncState } from '@/components/ui/async-state';
import { Button } from '@/components/ui/button';
import { ConfirmationDialog } from '@/components/ui/confirmation-dialog';
import { Input } from '@/components/ui/input';
import { ProgressState, StatusBadge } from '@/components/ui/status';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import {
  deleteKb,
  deleteKbFile,
  getKb,
  listKbFiles,
  reindexKbFile,
  uploadKbFile,
  type KbFileStatus,
} from '@/lib/api/kb';
import { useFormatDateTime } from '@/lib/timezone';
import { KnowledgeSourceExplorer } from '@/pages/knowledge/KnowledgeSourceExplorer';

const ACCEPTED_SOURCES = [
  '.pdf', '.docx', '.pptx', '.xlsx', '.csv', '.tsv', '.json', '.html',
  '.htm', '.md', '.markdown', '.txt', '.log', '.rst',
].join(',');

type SourceFilter = 'all' | KbFileStatus;
type KnowledgeTab = 'overview' | 'sources';

function errorState(error: unknown): 'permission' | 'error' {
  const message = error instanceof Error ? error.message : String(error ?? '');
  return /(?:\b403\b|forbidden|permission)/i.test(message) ? 'permission' : 'error';
}

export function KnowledgeDetailPage() {
  const { kbId = '' } = useParams<{ kbId: string }>();
  const [searchParams, setSearchParams] = useSearchParams();
  const navigate = useNavigate();
  const { t } = useTranslation();
  const formatTime = useFormatDateTime();
  const client = useQueryClient();
  const fileInput = useRef<HTMLInputElement>(null);
  const [sourceQuery, setSourceQuery] = useState('');
  const [sourceFilter, setSourceFilter] = useState<SourceFilter>('all');
  const [shareOpen, setShareOpen] = useState(false);
  const [deleteKbOpen, setDeleteKbOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<{ id: string; name: string } | null>(null);

  const detail = useQuery({
    queryKey: ['knowledge-base', kbId],
    queryFn: () => getKb(kbId),
    enabled: Boolean(kbId),
  });
  const files = useQuery({
    queryKey: ['knowledge-files', kbId],
    queryFn: () => listKbFiles(kbId),
    enabled: Boolean(kbId),
    refetchInterval: (query) => query.state.data?.some(
      (file) => file.status === 'pending' || file.status === 'indexing',
    ) ? 2000 : false,
  });
  const fileRevision = (files.data ?? [])
    .map((file) => `${file.id}:${file.status}:${file.chunk_count}`)
    .join('|');
  useEffect(() => {
    if (fileRevision) void detail.refetch();
    // Refetch aggregates only when a file state/count changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fileRevision]);

  const refresh = async () => Promise.all([detail.refetch(), files.refetch()]);
  const upload = useMutation({
    mutationFn: (file: File) => uploadKbFile(kbId, file),
    onSuccess: async () => {
      await Promise.all([
        client.invalidateQueries({ queryKey: ['knowledge-base', kbId] }),
        client.invalidateQueries({ queryKey: ['knowledge-files', kbId] }),
      ]);
      toast.success(t('knowledge.uploaded', 'File queued for indexing'));
    },
    onError: (reason) => toast.error(reason instanceof Error ? reason.message : String(reason)),
  });
  const reindex = useMutation({
    mutationFn: (fileId: string) => reindexKbFile(kbId, fileId),
    onSuccess: async () => {
      await client.invalidateQueries({ queryKey: ['knowledge-files', kbId] });
      toast.success(t('knowledge.reindexQueued', 'Reindex queued'));
    },
    onError: (reason) => toast.error(reason instanceof Error ? reason.message : String(reason)),
  });
  const removeFile = useMutation({
    mutationFn: (fileId: string) => deleteKbFile(kbId, fileId),
    onSuccess: async () => {
      await Promise.all([
        client.invalidateQueries({ queryKey: ['knowledge-base', kbId] }),
        client.invalidateQueries({ queryKey: ['knowledge-files', kbId] }),
      ]);
      setDeleteTarget(null);
      toast.success(t('knowledge.fileDeleted', 'File deleted'));
    },
    onError: (reason) => toast.error(reason instanceof Error ? reason.message : String(reason)),
  });
  const removeKnowledgeBase = useMutation({
    mutationFn: () => deleteKb(kbId),
    onSuccess: async () => {
      await client.invalidateQueries({ queryKey: ['knowledge-bases'] });
      toast.success(t('knowledge.deleted', 'Knowledge base deleted'));
      navigate('/knowledge', { replace: true });
    },
    onError: (reason) => toast.error(reason instanceof Error ? reason.message : String(reason)),
  });

  const visibleFiles = useMemo(
    () => (files.data ?? []).filter(
      (file) => {
        if (sourceFilter !== 'all' && file.status !== sourceFilter) return false;
        const normalized = sourceQuery.trim().toLocaleLowerCase();
        return !normalized || `${file.name} ${file.parser_type} ${file.error_message ?? ''}`
          .toLocaleLowerCase()
          .includes(normalized);
      },
    ),
    [files.data, sourceFilter, sourceQuery],
  );
  const sourceStatusCounts = useMemo(() => {
    const counts: Record<SourceFilter, number> = {
      all: files.data?.length ?? 0,
      pending: 0,
      indexing: 0,
      indexed: 0,
      failed: 0,
    };
    for (const file of files.data ?? []) counts[file.status] += 1;
    return counts;
  }, [files.data]);

  if (detail.isLoading) {
    return <AsyncState kind="loading" className="m-6" title={t('knowledge.loadingDetail', 'Loading knowledge base…')} />;
  }
  if (detail.isError || !detail.data) {
    const kind = errorState(detail.error);
    return (
      <AsyncState
        kind={kind}
        className="m-6"
        title={kind === 'permission'
          ? t('knowledge.forbiddenDetail', 'You do not have access to this knowledge base')
          : t('knowledge.detailFailed', 'Could not load this knowledge base')}
        description={kind === 'permission'
          ? t('knowledge.forbiddenDetailHint', 'Ask the knowledge owner for access.')
          : t('knowledge.detailFailedHint', 'Check the connection and try loading this knowledge base again.')}
        technicalDetails={detail.error instanceof Error ? detail.error.message : undefined}
        technicalDetailsLabel={t('common.technicalDetails', 'Technical details')}
        actionLabel={kind === 'error' ? t('retry', 'Retry') : undefined}
        onAction={kind === 'error' ? () => void detail.refetch() : undefined}
      />
    );
  }

  const indexed = (files.data ?? []).filter((file) => file.status === 'indexed').length;
  const unhealthy = (files.data ?? []).filter((file) => file.status === 'failed').length;
  const canUpdate = detail.data.access.capabilities.includes('update');
  const canShare = detail.data.access.capabilities.includes('manage_access');
  const canDelete = detail.data.access.capabilities.includes('delete');
  const requestedTab = searchParams.get('tab');
  const activeTab: KnowledgeTab = requestedTab === 'sources' ? 'sources' : 'overview';

  return (
    <>
      <EntityDetailShell
        resourceKind="knowledge"
        backTo="/knowledge"
        backLabel={t('knowledge.back', 'Knowledge')}
        title={detail.data.name}
        description={detail.data.description || t('knowledge.noDescription', 'No description')}
        icon={BookOpen}
        status={(
          <StatusBadge status={unhealthy ? 'warning' : indexed ? 'success' : 'neutral'}>
            {unhealthy
              ? t('knowledge.needsAttention', 'Needs attention')
              : indexed
                ? t('knowledge.ready', 'Ready')
                : t('knowledge.emptyIndex', 'Empty index')}
          </StatusBadge>
        )}
        metadata={(
          <>
            <span>{detail.data.file_count} {t('knowledge.files', 'files')}</span>
            <span>{detail.data.chunk_count} {t('knowledge.chunks', 'chunks')}</span>
            <span>{t('knowledge.agenticFiles', 'Agentic files')}</span>
          </>
        )}
        actions={(
          <>
            <Button variant="outline" size="sm" onClick={() => void refresh()}>
              <RefreshCw className="h-4 w-4" />{t('refresh', 'Refresh')}
            </Button>
            {canShare ? (
              <Button variant="outline" size="sm" onClick={() => setShareOpen(true)}>
                <Share2 className="h-4 w-4" />{t('share.share', 'Share')}
              </Button>
            ) : null}
            {canDelete ? (
              <Button
                variant="outline"
                size="sm"
                className="text-destructive hover:text-destructive"
                onClick={() => setDeleteKbOpen(true)}
              >
                <Trash2 className="h-4 w-4" />{t('knowledge.delete', 'Delete')}
              </Button>
            ) : null}
            {canUpdate && activeTab === 'sources' ? (
              <Button size="sm" onClick={() => fileInput.current?.click()}>
                <Upload className="h-4 w-4" />{t('knowledge.upload', 'Upload source')}
              </Button>
            ) : null}
            <input
              ref={fileInput}
              className="hidden"
              type="file"
              accept={ACCEPTED_SOURCES}
              onChange={(event) => {
                const file = event.target.files?.[0];
                if (file) upload.mutate(file);
                event.currentTarget.value = '';
              }}
            />
          </>
        )}
      >
        <Tabs
          value={activeTab}
          onValueChange={(tab) => {
            const next = new URLSearchParams(searchParams);
            next.set('tab', tab);
            setSearchParams(next, { replace: true });
          }}
          className="flex min-h-0 flex-1 flex-col gap-4"
        >
          <TabsList variant="underline" className="h-auto w-full shrink-0 justify-start">
            <TabsTrigger value="overview">
              {t('knowledge.tabs.overview', 'Overview')}
            </TabsTrigger>
            <TabsTrigger value="sources">
              {t('knowledge.tabs.sources', 'Sources')} · {files.data?.length ?? 0}
            </TabsTrigger>
          </TabsList>

          <TabsContent value="overview" className="page-scroll-region mt-0 min-h-0 flex-1 space-y-5 pr-2">
        <section aria-label={t('knowledge.indexHealth', 'Index health')} className="grid gap-3 sm:grid-cols-3">
          <div className="rounded-lg border border-edge-subtle p-4">
            <ProgressState
              status={files.isFetching ? 'running' : unhealthy ? 'warning' : 'success'}
              label={t('knowledge.indexHealth', 'Index health')}
              detail={`${indexed}/${files.data?.length ?? 0}`}
              value={indexed}
              max={Math.max(1, files.data?.length ?? 0)}
            />
          </div>
          <div className="rounded-lg border border-edge-subtle p-4">
            <div className="text-meta">{t('knowledge.totalChunks', 'Retrievable chunks')}</div>
            <div className="mt-1 text-2xl font-semibold tabular-nums">{detail.data.chunk_count}</div>
          </div>
          <div className="rounded-lg border border-edge-subtle p-4">
            <div className="text-meta">{t('knowledge.lastUpdated', 'Last updated')}</div>
            <div className="mt-2 text-sm font-medium">{formatTime(detail.data.latest_updated_at)}</div>
          </div>
        </section>

          </TabsContent>

          <TabsContent value="sources" className="page-scroll-region mt-0 min-h-0 flex-1 pr-2">

        <section className="space-y-3" aria-labelledby="knowledge-files-heading">
          <div>
            <div>
              <h2 id="knowledge-files-heading" className="text-section">{t('knowledge.sourceFiles', 'Source files')}</h2>
              <p className="text-sm text-content-secondary">
                {t('knowledge.sourceFilesHint', 'PDF, Office, text, web, JSON and tabular sources. Only indexed files are available to retrieval.')}
              </p>
            </div>
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <div className="relative min-w-56 flex-1 sm:max-w-sm">
                <Search className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-content-tertiary" />
                <Input
                  value={sourceQuery}
                  onChange={(event) => setSourceQuery(event.target.value)}
                  className="pl-9"
                  placeholder={t('knowledge.searchSources', 'Search source files')}
                />
              </div>
              <div className="flex flex-wrap rounded-lg bg-surface-sunken p-1" aria-label={t('knowledge.filterSources', 'Filter sources')}>
              {(['all', 'pending', 'indexing', 'indexed', 'failed'] as const).map((filter) => (
                <Button
                  key={filter}
                  size="sm"
                  variant={sourceFilter === filter ? 'secondary' : 'ghost'}
                  className="h-7 px-2.5 text-xs"
                  onClick={() => setSourceFilter(filter)}
                >
                  {filter === 'all'
                    ? t('all', 'All')
                    : t(`knowledge.fileStatus.${filter}`, filter)}
                  <span className="ml-1 tabular-nums text-content-tertiary">
                    {sourceStatusCounts[filter]}
                  </span>
                </Button>
              ))}
              </div>
            </div>
          </div>
          {files.isLoading ? <AsyncState kind="loading" title={t('knowledge.loadingFiles', 'Loading files…')} /> : null}
          {files.isError ? (
            <AsyncState
              kind={errorState(files.error)}
              title={errorState(files.error) === 'permission'
                ? t('knowledge.filesForbidden', 'You do not have access to source files')
                : t('knowledge.filesFailed', 'Could not load source files')}
              actionLabel={errorState(files.error) === 'error' ? t('retry', 'Retry') : undefined}
              onAction={errorState(files.error) === 'error' ? () => void files.refetch() : undefined}
            />
          ) : null}
          {unhealthy > 0 ? (
            <AsyncState
              kind="partial"
              title={t('knowledge.partialIndex', '{{count}} source files need attention', { count: unhealthy })}
              description={t('knowledge.partialIndexHint', 'Indexed sources remain searchable. Review the failed rows and reindex them individually.')}
            />
          ) : null}
          {!files.isLoading && !files.isError && !(files.data?.length) ? (
            <AsyncState
              kind="empty"
              title={t('knowledge.noFiles', 'No source files')}
              description={t('knowledge.noFilesHint', 'Upload a document to create the first searchable chunks.')}
              actionLabel={canUpdate ? t('knowledge.upload', 'Upload source') : undefined}
              onAction={canUpdate ? () => fileInput.current?.click() : undefined}
            />
          ) : null}
          {files.data?.length && !visibleFiles.length ? (
            <AsyncState
              kind="empty"
              title={t('knowledge.noFilesForFilter', 'No sources match this filter')}
              description={sourceQuery
                ? t('knowledge.noSourceSearchHint', 'Try another file name or clear the search.')
                : undefined}
            />
          ) : null}
          {visibleFiles.length ? (
            <KnowledgeSourceExplorer
              kbId={kbId}
              files={visibleFiles}
              canUpdate={canUpdate}
              reindexing={reindex.isPending}
              deleting={removeFile.isPending}
              formatTime={formatTime}
              onReindex={(fileId) => reindex.mutate(fileId)}
              onDelete={(file) => setDeleteTarget({ id: file.id, name: file.name })}
            />
          ) : null}
        </section>
          </TabsContent>

        </Tabs>

        <ConfirmationDialog
          open={deleteKbOpen}
          onOpenChange={(open) => {
            if (!removeKnowledgeBase.isPending) setDeleteKbOpen(open);
          }}
          title={t('knowledge.deleteTitle', 'Delete this knowledge base?')}
          description={t(
            'knowledge.deleteDescription',
            '{{name}} and all of its source files will be removed from active retrieval.',
            { name: detail.data.name },
          )}
          confirmLabel={removeKnowledgeBase.isPending ? t('deleting', 'Deleting…') : t('delete', 'Delete')}
          cancelLabel={t('cancel', 'Cancel')}
          pending={removeKnowledgeBase.isPending}
          onConfirm={() => removeKnowledgeBase.mutate()}
        />

        <ConfirmationDialog
          open={deleteTarget !== null}
          onOpenChange={(open) => {
            if (!open && !removeFile.isPending) setDeleteTarget(null);
          }}
          title={t('knowledge.deleteFileTitle', 'Delete source file?')}
          description={t('knowledge.deleteFileDescription', '{{name}} and its indexed chunks will be removed from retrieval.', { name: deleteTarget?.name ?? '' })}
          confirmLabel={removeFile.isPending ? t('deleting', 'Deleting…') : t('delete', 'Delete')}
          cancelLabel={t('cancel', 'Cancel')}
          pending={removeFile.isPending}
          onConfirm={() => {
            if (deleteTarget) removeFile.mutate(deleteTarget.id);
          }}
        />
      </EntityDetailShell>

      <ResourceShareDialog
        open={shareOpen}
        onOpenChange={setShareOpen}
        resourceKind="knowledge_base"
        resourceId={detail.data.id}
        resourceName={detail.data.name}
        effectiveRole={detail.data.access.effective_role}
        accessSource={detail.data.access.source}
      />
    </>
  );
}
