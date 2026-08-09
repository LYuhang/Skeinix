import { useMemo, useRef, useState, type KeyboardEvent } from 'react';
import { useInfiniteQuery } from '@tanstack/react-query';
import { ChevronRight, RefreshCw, Trash2 } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import { FileTypeIcon } from '@/components/presentation/FileTypeIcon';
import { Button } from '@/components/ui/button';
import { PaneResizeHandle } from '@/components/ui/pane-resize-handle';
import { StatusBadge, type SemanticStatus } from '@/components/ui/status';
import { usePersistedPaneWidth } from '@/components/ui/use-persisted-pane-width';
import { formatBytes } from '@/lib/format/bytes';
import { cn } from '@/lib/utils';
import { getKbFileContent, type KbFile, type KbFileStatus } from '@/lib/api/kb';

type TreeNode = {
  name: string;
  path: string;
  kind: 'folder' | 'file';
  file?: KbFile;
  children: TreeNode[];
};

type VisibleItem = { node: TreeNode; depth: number; parentPath: string | null };

const fileTone: Record<KbFileStatus, SemanticStatus> = {
  pending: 'neutral',
  indexing: 'running',
  indexed: 'success',
  failed: 'danger',
};

function buildTree(files: KbFile[]): TreeNode[] {
  const roots: TreeNode[] = [];
  for (const file of files) {
    const parts = file.name.split('/').filter(Boolean);
    let siblings = roots;
    let parentPath = '';
    parts.forEach((name, index) => {
      const path = parentPath ? `${parentPath}/${name}` : name;
      const kind = index === parts.length - 1 ? 'file' : 'folder';
      let node = siblings.find((candidate) => candidate.name === name && candidate.kind === kind);
      if (!node) {
        node = { name, path, kind, children: [], ...(kind === 'file' ? { file } : {}) };
        siblings.push(node);
      }
      siblings = node.children;
      parentPath = path;
    });
  }
  const sort = (nodes: TreeNode[]) => {
    nodes.sort((left, right) => {
      if (left.kind !== right.kind) return left.kind === 'folder' ? -1 : 1;
      return left.name.localeCompare(right.name);
    });
    nodes.forEach((node) => sort(node.children));
  };
  sort(roots);
  return roots;
}

function folders(nodes: TreeNode[]): string[] {
  return nodes.flatMap((node) => node.kind === 'folder'
    ? [node.path, ...folders(node.children)]
    : []);
}

function visibleItems(
  nodes: TreeNode[],
  expanded: Set<string>,
  depth = 0,
  parentPath: string | null = null,
): VisibleItem[] {
  return nodes.flatMap((node) => [
    { node, depth, parentPath },
    ...(node.kind === 'folder' && expanded.has(node.path)
      ? visibleItems(node.children, expanded, depth + 1, node.path)
      : []),
  ]);
}

export function KnowledgeSourceExplorer({
  kbId,
  files,
  canUpdate,
  reindexing,
  deleting,
  formatTime,
  onReindex,
  onDelete,
}: {
  kbId: string;
  files: KbFile[];
  canUpdate: boolean;
  reindexing: boolean;
  deleting: boolean;
  formatTime: (value: string | null | undefined) => string;
  onReindex: (fileId: string) => void;
  onDelete: (file: KbFile) => void;
}) {
  const { t } = useTranslation();
  const pane = usePersistedPaneWidth({
    storageKey: `vibecanvas:knowledge-source-tree-width:v1:${kbId}`,
    defaultWidth: 272,
    minWidth: 220,
    maxWidth: 400,
  });
  const tree = useMemo(() => buildTree(files), [files]);
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set(folders(tree)));
  const [selectedId, setSelectedId] = useState<string | null>(null);
  // Surface an actionable indexing failure immediately. Once the user picks a
  // file, preserve that explicit selection across refetches.
  const selected = files.find((file) => file.id === selectedId)
    ?? files.find((file) => file.status === 'failed')
    ?? files[0]
    ?? null;
  const content = useInfiniteQuery({
    queryKey: ['knowledge-file-content', kbId, selected?.id],
    queryFn: ({ pageParam }) => getKbFileContent(kbId, selected!.id, pageParam, 50),
    initialPageParam: 0,
    getNextPageParam: (lastPage) => lastPage.has_more ? lastPage.next_offset : undefined,
    enabled: Boolean(selected?.id && selected.status === 'indexed'),
  });
  const contentChunks = content.data?.pages.flatMap((page) => page.chunks) ?? [];
  const rows = useMemo(() => visibleItems(tree, expanded), [expanded, tree]);
  const refs = useRef(new Map<string, HTMLButtonElement>());

  const toggle = (path: string) => setExpanded((current) => {
    const next = new Set(current);
    if (next.has(path)) next.delete(path);
    else next.add(path);
    return next;
  });
  const focusAt = (index: number) => {
    const row = rows[index];
    if (row) refs.current.get(row.node.path)?.focus();
  };
  const onKeyDown = (event: KeyboardEvent<HTMLButtonElement>, row: VisibleItem) => {
    const index = rows.findIndex(({ node }) => node.path === row.node.path);
    if (event.key === 'ArrowDown') {
      event.preventDefault(); focusAt(Math.min(rows.length - 1, index + 1));
    } else if (event.key === 'ArrowUp') {
      event.preventDefault(); focusAt(Math.max(0, index - 1));
    } else if (event.key === 'ArrowRight' && row.node.kind === 'folder') {
      event.preventDefault();
      if (!expanded.has(row.node.path)) toggle(row.node.path);
      else if (row.node.children[0]) refs.current.get(row.node.children[0].path)?.focus();
    } else if (event.key === 'ArrowLeft') {
      event.preventDefault();
      if (row.node.kind === 'folder' && expanded.has(row.node.path)) toggle(row.node.path);
      else if (row.parentPath) refs.current.get(row.parentPath)?.focus();
    } else if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      if (row.node.kind === 'folder') toggle(row.node.path);
      else if (row.node.file) setSelectedId(row.node.file.id);
    }
  };

  return (
    <div className="flex min-h-[28rem] overflow-hidden rounded-lg border border-edge-subtle bg-background">
      <aside
        className="relative flex min-h-0 shrink-0 flex-col border-r border-edge-structural bg-surface-nav"
        style={{ width: pane.width }}
      >
        <PaneResizeHandle
          side="right"
          width={pane.width}
          minWidth={220}
          maxWidth={400}
          onWidthChange={pane.setWidth}
          onReset={pane.resetWidth}
          label={t('knowledge.resizeSources', 'Resize source explorer')}
        />
        <div className="flex h-10 shrink-0 items-center justify-between border-b px-3">
          <div className="flex min-w-0 items-center gap-2 text-xs font-medium">
            <FileTypeIcon directory className="size-5 rounded-none bg-transparent" />
            <span className="truncate">{t('knowledge.sourceFiles', 'Source files')}</span>
          </div>
          <span className="text-xs tabular-nums text-muted-foreground">{files.length}</span>
        </div>
        <div className="min-h-0 flex-1 overflow-auto py-1" role="tree" aria-label={t('knowledge.sourceFiles', 'Source files')}>
          {rows.map((row) => {
            const { node } = row;
            const isSelected = node.file?.id === selected?.id;
            return (
              <button
                key={`${node.kind}:${node.path}`}
                ref={(element) => {
                  if (element) refs.current.set(node.path, element);
                  else refs.current.delete(node.path);
                }}
                type="button"
                role="treeitem"
                aria-expanded={node.kind === 'folder' ? expanded.has(node.path) : undefined}
                aria-selected={node.kind === 'file' ? isSelected : undefined}
                onClick={() => node.kind === 'folder'
                  ? toggle(node.path)
                  : node.file && setSelectedId(node.file.id)}
                onKeyDown={(event) => onKeyDown(event, row)}
                className={cn(
                  'flex h-8 w-full min-w-0 items-center gap-1.5 rounded-sm pr-2 text-left text-xs outline-none hover:bg-surface-hover focus-visible:ring-2 focus-visible:ring-ring',
                  isSelected && 'bg-surface-selected text-content-primary',
                )}
                style={{ paddingLeft: 8 + row.depth * 16 }}
                title={node.path}
              >
                {node.kind === 'folder' ? (
                  <ChevronRight className={cn('size-3.5 shrink-0 transition-transform', expanded.has(node.path) && 'rotate-90')} />
                ) : <span className="w-3.5 shrink-0" />}
                <FileTypeIcon fileName={node.name} directory={node.kind === 'folder'} className="size-5 shrink-0 rounded-none bg-transparent" />
                <span className="min-w-0 flex-1 truncate">{node.name}</span>
                {node.file ? (
                  <span
                    className={cn(
                      'size-1.5 shrink-0 rounded-full',
                      node.file.status === 'indexed' && 'bg-state-success',
                      (node.file.status === 'pending' || node.file.status === 'indexing') && 'bg-state-running',
                      node.file.status === 'failed' && 'bg-state-danger',
                    )}
                    aria-label={t(`knowledge.fileStatus.${node.file.status}`, node.file.status)}
                  />
                ) : null}
              </button>
            );
          })}
        </div>
      </aside>

      <section className="min-w-0 flex-1 overflow-auto p-5" aria-live="polite">
        {selected ? (
          <div className="mx-auto max-w-2xl">
            <div className="flex min-w-0 items-start gap-3">
              <FileTypeIcon fileName={selected.name} className="mt-0.5 shrink-0" />
              <div className="min-w-0 flex-1">
                <h3 className="truncate text-sm font-semibold" title={selected.name}>{selected.name}</h3>
                <p className="mt-1 text-xs text-content-tertiary">{selected.parser_type.toUpperCase()}</p>
              </div>
              <StatusBadge status={fileTone[selected.status]}>
                {t(`knowledge.fileStatus.${selected.status}`, selected.status)}
              </StatusBadge>
            </div>
            <dl className="mt-5 grid gap-x-8 gap-y-4 border-t border-edge-subtle pt-5 sm:grid-cols-2">
              <div><dt className="text-meta">{t('knowledge.fileSize', 'File size')}</dt><dd className="mt-1 text-sm">{formatBytes(selected.file_size)}</dd></div>
              <div><dt className="text-meta">{t('knowledge.chunks', 'Chunks')}</dt><dd className="mt-1 text-sm tabular-nums">{selected.chunk_count}</dd></div>
              <div><dt className="text-meta">{t('knowledge.fileStatusLabel', 'Index status')}</dt><dd className="mt-1 text-sm">{t(`knowledge.fileStatus.${selected.status}`, selected.status)}</dd></div>
              <div><dt className="text-meta">{t('knowledge.addedAt', 'Added')}</dt><dd className="mt-1 text-sm">{formatTime(selected.created_at)}</dd></div>
            </dl>
            <div className="mt-5 border-t border-edge-subtle pt-5">
              <div className="mb-2 flex items-center justify-between gap-3">
                <h4 className="text-sm font-medium">{t('knowledge.parsedContent', 'Parsed content')}</h4>
                {content.data?.pages[0] ? (
                  <span className="text-xs tabular-nums text-content-tertiary">
                    {t('knowledge.contentSections', '{{loaded}} of {{total}} sections', {
                      loaded: contentChunks.length,
                      total: content.data.pages[0].total_chunks,
                    })}
                  </span>
                ) : null}
              </div>
              {selected.status === 'indexed' && content.isPending ? (
                <div className="flex min-h-40 items-center justify-center gap-2 rounded-md border border-edge-subtle bg-surface-sunken text-sm text-content-secondary">
                  <RefreshCw className="size-4 animate-spin" />
                  {t('knowledge.loadingContent', 'Loading file content…')}
                </div>
              ) : null}
              {selected.status === 'indexed' && content.isError ? (
                <div className="rounded-md border border-state-danger/30 bg-state-danger/5 p-4 text-sm text-state-danger">
                  <p>{t('knowledge.contentFailed', 'Could not load this file’s content.')}</p>
                  <Button className="mt-3" variant="outline" size="sm" onClick={() => void content.refetch()}>
                    <RefreshCw className="size-4" />
                    {t('retry', 'Retry')}
                  </Button>
                </div>
              ) : null}
              {selected.status === 'indexed' && content.isSuccess && contentChunks.length ? (
                <div className="max-h-[34rem] overflow-auto rounded-md border border-edge-subtle bg-surface-sunken p-4">
                  {contentChunks.map((chunk) => (
                    <pre
                      key={chunk.index}
                      className="mb-4 whitespace-pre-wrap break-words font-mono text-xs leading-5 text-content-primary last:mb-0"
                    >
                      {chunk.text}
                    </pre>
                  ))}
                </div>
              ) : null}
              {selected.status === 'indexed' && content.isSuccess && !contentChunks.length ? (
                <div className="rounded-md border border-edge-subtle bg-surface-sunken p-4 text-sm text-content-secondary">
                  {t('knowledge.noParsedContent', 'No readable text was extracted from this file.')}
                </div>
              ) : null}
              {selected.status !== 'indexed' ? (
                <div className="rounded-md border border-edge-subtle bg-surface-sunken p-4 text-sm text-content-secondary">
                  {selected.status === 'failed'
                    ? t('knowledge.contentUnavailable', 'Parsed content is unavailable because indexing failed.')
                    : t('knowledge.contentPending', 'Parsed content will appear when indexing finishes.')}
                </div>
              ) : null}
              {content.hasNextPage ? (
                <Button
                  className="mt-3"
                  variant="outline"
                  size="sm"
                  disabled={content.isFetchingNextPage}
                  onClick={() => void content.fetchNextPage()}
                >
                  {content.isFetchingNextPage ? <RefreshCw className="size-4 animate-spin" /> : null}
                  {content.isFetchingNextPage
                    ? t('knowledge.loadingMoreContent', 'Loading more…')
                    : t('knowledge.loadMoreContent', 'Load more')}
                </Button>
              ) : null}
            </div>
            {selected.error_message ? (
              <div className="mt-5 rounded-md border border-state-danger/30 bg-state-danger/5 p-3 text-sm text-state-danger">
                {selected.error_message}
              </div>
            ) : null}
            {canUpdate ? (
              <div className="mt-5 flex flex-wrap gap-2 border-t border-edge-subtle pt-4">
                <Button
                  variant="outline"
                  size="sm"
                  disabled={reindexing || selected.status === 'pending' || selected.status === 'indexing'}
                  onClick={() => onReindex(selected.id)}
                >
                  <RefreshCw className={cn('h-4 w-4', reindexing && 'animate-spin')} />
                  {t('knowledge.reindex', 'Reindex')}
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={deleting || selected.status === 'indexing'}
                  onClick={() => onDelete(selected)}
                  aria-label={t('knowledge.deleteFile', 'Delete {{name}}', { name: selected.name })}
                >
                  <Trash2 className="h-4 w-4" />
                  {t('delete', 'Delete')}
                </Button>
              </div>
            ) : null}
          </div>
        ) : null}
      </section>
    </div>
  );
}
