import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useVfsList } from '@/lib/api/queries/vfs';
import type { VfsUploadFolder } from '@/lib/api/vfs';
import { buildFileTree, type FileTreeNode } from './fileTree';
import { CollapsibleFolder } from './CollapsibleFolder';
import { VfsItemMenu } from './VfsItemMenu';
import { formatBytes } from '@/lib/format/bytes';
import { TriangleAlert } from 'lucide-react';
import { FileTypeIcon } from '@/components/presentation/FileTypeIcon';

export interface VfsFilesSectionProps {
  wfId: string;
  open: boolean;
  onOpenFile: (path: string) => void;
  roots?: readonly string[];
  /**
   * Optional shared selection state. Explorer surfaces that compose multiple
   * VFS scopes should control this value so only one row is selected across
   * the whole visual tree.
   */
  selectionKey?: string | null;
  onSelectionKeyChange?: (key: string) => void;
  defaultSelectFirst?: boolean;
}

// One-line taxonomy semantics for the canonical top folders. Surfaced both as a
// hover tooltip on the depth-0 folder rows AND as the per-folder empty-state
// description (so an empty folder explains its own purpose). i18n keys live in
// `vfs.folder_*`.
const FOLDER_TOOLTIP_KEY: Record<string, string> = {
  mount: 'vfs.folder_mount',
  data: 'vfs.folder_data',
  memory: 'vfs.folder_memory',
  logs: 'vfs.folder_logs',
  skills: 'vfs.folder_skills',
};

// Depth-0 folders that accept user uploads (durable artifact prefixes). Mirrors
// the backend `_UPLOAD_FOLDERS` allowlist.
const UPLOAD_FOLDERS: readonly VfsUploadFolder[] = ['mount', 'data'];

export function VfsFilesSection({
  wfId,
  open,
  onOpenFile,
  roots,
  selectionKey,
  onSelectionKeyChange,
  defaultSelectFirst = true,
}: VfsFilesSectionProps) {
  const { t } = useTranslation();
  const q = useVfsList(wfId, { enabled: open });
  const expansionKey = `vibecanvas:vfs-expanded:v1:${wfId}:${(roots ?? []).join(',') || 'all'}`;
  const [expanded, setExpanded] = useState<Set<string>>(() => {
    try { return new Set(JSON.parse(localStorage.getItem(expansionKey) ?? '[]') as string[]); }
    catch { return new Set(); }
  });
  const [selected, setSelected] = useState<string | null>(null);
  const selectionIsControlled = selectionKey !== undefined;
  const itemSelectionKey = (path: string) => `${wfId}:${path}`;
  const selectItem = (path: string) => {
    if (selectionIsControlled) onSelectionKeyChange?.(itemSelectionKey(path));
    else setSelected(path);
  };
  const typeahead = useRef({ value: '', at: 0 });
  useEffect(() => {
    try { localStorage.setItem(expansionKey, JSON.stringify([...expanded])); } catch { /* optional UI state */ }
  }, [expanded, expansionKey]);
  useEffect(() => {
    queueMicrotask(() => {
      try { setExpanded(new Set(JSON.parse(localStorage.getItem(expansionKey) ?? '[]') as string[])); }
      catch { setExpanded(new Set()); }
      if (!selectionIsControlled) setSelected(null);
    });
  }, [expansionKey, selectionIsControlled]);
  const toggle = (path: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });

  if (q.isLoading) return <div className="px-3 py-2 text-meta">{t('vfs.loading', 'Loading…')}</div>;
  if (q.isError) return <div className="px-3 py-2 text-meta text-destructive">{t('vfs.files_error', 'Failed to load files.')}</div>;

  const entries = q.data?.entries ?? [];
  const rootCapabilities = q.data?.root_capabilities ?? {};
  const visibleRoots = roots ? new Set(roots) : null;
  const tree = buildFileTree(entries).filter((node) => !visibleRoots || visibleRoots.has(node.name));

  const renderNode = (node: FileTreeNode, depth: number): React.ReactNode => {
    const isSelected = selectionIsControlled
      ? selectionKey === itemSelectionKey(node.path) || (
          selectionKey === null &&
          defaultSelectFirst &&
          depth === 0 &&
          tree[0]?.path === node.path
        )
      : selected === node.path || (
          selected === null &&
          depth === 0 &&
          tree[0]?.path === node.path
        );
    if (node.kind === 'file') {
      const e = node.entry!;
      return (
        <VfsItemMenu
          key={node.path}
          path={e.path}
          name={node.name}
          isFolder={false}
          wfId={wfId}
          capabilities={e.capabilities}
        >
          <button
            type="button"
            role="treeitem"
            aria-level={depth + 1}
            aria-selected={isSelected}
            data-tree-full-path={node.path}
            data-tree-name={node.name.toLocaleLowerCase()}
            data-tree-kind="file"
            tabIndex={isSelected ? 0 : -1}
            className="interactive-row flex w-full items-start gap-2 rounded py-1.5 text-left text-ui aria-selected:bg-surface-hover aria-selected:text-content-primary"
            style={{ paddingLeft: depth * 12 + 8 }}
            onClick={() => {
              selectItem(node.path);
              onOpenFile(e.path);
            }}
            onDoubleClick={() => onOpenFile(e.path)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') {
                event.preventDefault();
                onOpenFile(e.path);
              }
            }}
          >
            <FileTypeIcon
              fileName={node.name}
              mimeType={e.content_type}
              className="size-6 rounded-md"
            />
            <span className="min-w-0 flex-1">
              <span className="block truncate font-mono">{node.name}</span>
              <span className="mt-0.5 block truncate text-xs text-content-tertiary">
                {e.content_type} · {formatBytes(e.size_bytes)}
                {e.stale ? (
                  <span
                    className="ml-1 inline-flex items-center gap-1 text-state-warning"
                    aria-label={t('vfs.stale_file', 'Stale file')}
                    title={t(
                      'vfs.stale_file_help',
                      'This file was produced from an older workflow version.',
                    )}
                  >
                    · <TriangleAlert aria-hidden className="h-3 w-3" />
                    {t('vfs.stale_marker', 'stale')}
                  </span>
                ) : null}
              </span>
            </span>
          </button>
        </VfsItemMenu>
      );
    }
    const isOpen = expanded.has(node.path);
    const indent = { paddingLeft: (depth + 1) * 12 + 8 };
    // Upload is offered on the user-writable root folders (`/mount`, `/data`)
    // via the right-click menu's "Upload file…" action; /memory/logs/skills are
    // agent-owned and get no upload control.
    const rootName = node.path.split('/').filter(Boolean)[0] ?? node.name;
    const rootCaps = rootCapabilities[rootName] ?? [];
    const uploadFolder =
      depth === 0 && rootCaps.includes('upload') && (UPLOAD_FOLDERS as readonly string[]).includes(node.name)
        ? (node.name as VfsUploadFolder)
        : undefined;
    // Direct-child filenames of this folder — for the upload overwrite prompt.
    const uploadExistingNames = uploadFolder
      ? entries
          .filter((e) => e.path.startsWith(`/${uploadFolder}/`) && !e.path.slice(uploadFolder.length + 2).includes('/'))
          .map((e) => e.path.split('/').pop() as string)
      : undefined;
    // The folder's taxonomy meaning — shown as a hover tooltip on the row AND,
    // when the folder is empty, as its own empty-state description (so an empty
    // depth-0 folder explains its purpose instead of the generic line).
    const tipKey = depth === 0 ? FOLDER_TOOLTIP_KEY[node.name] : undefined;
    const folderPurpose = tipKey ? t(tipKey, '') || undefined : undefined;
    const folder = (
      <CollapsibleFolder
        key={node.path}
        label={node.name}
        path={node.path}
        depth={depth}
        open={isOpen}
        treeItem
        onToggle={() => toggle(node.path)}
        selected={isSelected}
        onSelect={() => selectItem(node.path)}
        title={folderPurpose}
      >
        {node.children.length === 0 ? (
          <div className="py-1 text-meta" style={indent}>
            {folderPurpose ? (
              <>
                {folderPurpose}{' '}
                <span className="text-muted-foreground/70">{t('vfs.empty_hint', '(empty)')}</span>
              </>
            ) : (
              t('vfs.empty_files', 'No files yet — the agent writes files here as it works.')
            )}
          </div>
        ) : (
          node.children.map((c) => renderNode(c, depth + 1))
        )}
      </CollapsibleFolder>
    );
    // A right-click context menu on the folder block. Folders under /mount|/data
    // are user-modifiable (Rename/Delete + Copy Path, no Download); other folders
    // (canonical roots like /memory, /logs) get Copy Path only. The whole folder
    // block is the trigger (triggerAsChild=false → a div wrapper); nested file
    // rows keep their own inner triggers (radix uses the innermost).
    return (
      <VfsItemMenu
        key={node.path}
        path={node.path}
        name={node.name}
        isFolder
        wfId={wfId}
        capabilities={[
          'read',
          'copy_path',
          ...(rootCaps.includes('rename') ? (['rename'] as const) : []),
          ...(rootCaps.includes('delete') ? (['delete'] as const) : []),
        ]}
        triggerAsChild={false}
        uploadFolder={uploadFolder}
        uploadExistingNames={uploadExistingNames}
      >
        {folder}
      </VfsItemMenu>
    );
  };

  const moveFocus = (event: React.KeyboardEvent<HTMLDivElement>, direction: number | 'first' | 'last') => {
    const items = Array.from(event.currentTarget.querySelectorAll<HTMLElement>('[role="treeitem"]'));
    if (!items.length) return;
    const current = document.activeElement as HTMLElement | null;
    const index = Math.max(0, items.indexOf(current as HTMLElement));
    const next = direction === 'first' ? items[0]
      : direction === 'last' ? items[items.length - 1]
      : items[Math.max(0, Math.min(items.length - 1, index + direction))];
    event.preventDefault();
    next?.focus();
    const nextPath = next?.dataset.treeFullPath;
    if (nextPath) selectItem(nextPath);
  };

  return (
    <div
      role="tree"
      aria-label={t('vfs.files', 'Files')}
      onKeyDown={(event) => {
        if (event.defaultPrevented) return;
        if (event.key === 'ArrowDown') return moveFocus(event, 1);
        if (event.key === 'ArrowUp') return moveFocus(event, -1);
        if (event.key === 'Home') return moveFocus(event, 'first');
        if (event.key === 'End') return moveFocus(event, 'last');
        if (event.key.length !== 1 || event.ctrlKey || event.metaKey || event.altKey) return;
        const now = Date.now();
        const previous = now - typeahead.current.at < 700 ? typeahead.current.value : '';
        const value = `${previous}${event.key.toLocaleLowerCase()}`;
        typeahead.current = { value, at: now };
        const items = Array.from(event.currentTarget.querySelectorAll<HTMLElement>('[role="treeitem"]'));
        const match = items.find((item) => item.dataset.treeName?.startsWith(value));
        if (match) {
          event.preventDefault();
          match.focus();
          const matchPath = match.dataset.treeFullPath;
          if (matchPath) selectItem(matchPath);
        }
      }}
    >
      {tree.map((node) => renderNode(node, 0))}
    </div>
  );
}
