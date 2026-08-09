import { useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import { Download, Copy, Pencil, Trash2, Upload } from 'lucide-react';
import {
  ContextMenu,
  ContextMenuTrigger,
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuSeparator,
} from '@/components/ui/context-menu';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogFooter,
  DialogTitle,
  DialogDescription,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { signVfs, type VfsItemCapability, type VfsUploadFolder } from '@/lib/api/vfs';
import { useDeleteVfs, useRenameVfs, useUploadVfsFile } from '@/lib/api/queries/vfs';
import { formatBytes } from '@/lib/format/bytes';

/** The parent folder of a VFS path (`/data/a/b.txt` → `/data/a`; `/data` → ``). */
function parentOf(path: string): string {
  const i = path.lastIndexOf('/');
  return i <= 0 ? '' : path.slice(0, i);
}

export interface VfsItemMenuProps {
  /** Full VFS path of the item the menu acts on. */
  path: string;
  /** Display name (last segment) — the rename input prefill + download filename. */
  name: string;
  /** Folders hide Download (only files are downloadable). */
  isFolder: boolean;
  /** Durable-VFS scope for sign/delete/rename. Omit for run-tier items. */
  wfId?: string;
  /** Run-tier scope for sign(). Mutually exclusive with wfId in practice. */
  runId?: string;
  /** Authoritative actions returned by the backend for this path. */
  capabilities: readonly VfsItemCapability[];
  /**
   * When true (default) the menu trigger merges onto the single child element
   * (`asChild`). Folder blocks pass false so the whole CollapsibleFolder (a
   * non-ref-forwarding component) becomes the trigger via a wrapping element.
   */
  triggerAsChild?: boolean;
  /**
   * When set (the user-writable root folders `/mount` / `/data`), the menu adds
   * an "Upload file…" action that POSTs a picked file into this folder. Replaces
   * the old always-visible upload button.
   */
  uploadFolder?: VfsUploadFolder;
  /** Existing direct-child filenames of `uploadFolder`, for the overwrite prompt. */
  uploadExistingNames?: string[];
  children: React.ReactNode;
}

/**
 * VSCode-style right-click context menu for an Explorer file/folder row —
 * Download / Copy Path / Rename / Delete. Left-click behavior (open the file)
 * is unchanged: the menu only handles the contextmenu (right-click) gesture.
 * Treats the VFS as the user's cloud computer disk.
 */
export function VfsItemMenu({
  path,
  name,
  isFolder,
  wfId,
  runId,
  capabilities,
  triggerAsChild = true,
  uploadFolder,
  uploadExistingNames,
  children,
}: VfsItemMenuProps) {
  const { t } = useTranslation();
  const del = useDeleteVfs(wfId);
  const rename = useRenameVfs(wfId);
  // Hook is always created (rules-of-hooks); only used when uploadFolder is set.
  const upload = useUploadVfsFile(wfId, uploadFolder ?? 'data');
  const uploadInputRef = useRef<HTMLInputElement>(null);
  const [renameOpen, setRenameOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [pendingOverwrite, setPendingOverwrite] = useState<File | null>(null);
  const [draftName, setDraftName] = useState(name);

  const uploadFile = (file: File) => {
    if (!uploadFolder) return;
    const dir = `/${uploadFolder}`;
    upload.mutate(file, {
      onSuccess: (res) => {
        setPendingOverwrite(null);
        toast.success(
          res.replaced
            ? t('vfs.upload_replaced', { name: file.name, folder: dir, defaultValue: 'Overwrote {{name}} in {{folder}}.' })
            : t('vfs.upload_success', { name: file.name, folder: dir, defaultValue: 'Uploaded {{name}} to {{folder}}.' }),
        );
      },
      onError: () => toast.error(t('vfs.upload_error', 'Upload failed.')),
    });
  };

  const onPickUpload = (file: File) => {
    if (uploadExistingNames?.includes(file.name)) {
      setPendingOverwrite(file);
      return;
    }
    uploadFile(file);
  };

  const onDownload = async () => {
    try {
      const { url } = await signVfs({ path, wf_id: wfId, run_id: runId });
      const a = document.createElement('a');
      a.href = url;
      a.download = name;
      document.body.appendChild(a);
      a.click();
      a.remove();
    } catch {
      toast.error(t('vfs.download_failed', 'Download failed.'));
    }
  };

  const onCopyPath = async () => {
    try {
      await navigator.clipboard.writeText(path);
      toast.success(t('vfs.path_copied', 'Path copied.'));
    } catch {
      toast.error(t('vfs.copy_failed', 'Copy failed.'));
    }
  };

  const submitRename = () => {
    const next = draftName.trim();
    if (!next || next.includes('/')) {
      toast.error(t('vfs.rename_invalid', 'Enter a name without "/".'));
      return;
    }
    const newPath = `${parentOf(path)}/${next}`;
    if (newPath === path) {
      setRenameOpen(false);
      return;
    }
    rename.mutate(
      { old_path: path, new_path: newPath },
      {
        onSuccess: () => {
          setRenameOpen(false);
          toast.success(t('vfs.renamed', 'Renamed.'));
        },
        onError: () => toast.error(t('vfs.rename_failed', 'Rename failed.')),
      },
    );
  };

  const submitDelete = () => {
    del.mutate(path, {
      onSuccess: () => {
        setDeleteOpen(false);
        toast.success(t('vfs.deleted', 'Deleted.'));
      },
      onError: () => toast.error(t('vfs.delete_failed', 'Delete failed.')),
    });
  };

  return (
    <>
      <ContextMenu>
        <ContextMenuTrigger asChild={triggerAsChild}>{children}</ContextMenuTrigger>
        <ContextMenuContent className="w-44">
          {uploadFolder && (
            <ContextMenuItem onSelect={() => uploadInputRef.current?.click()} disabled={upload.isPending}>
              <Upload className="mr-2 h-4 w-4" />
              {t('vfs.upload_menu', 'Upload file…')}
            </ContextMenuItem>
          )}
          {!isFolder && capabilities.includes('download') && (
            <ContextMenuItem onSelect={() => void onDownload()}>
              <Download className="mr-2 h-4 w-4" />
              {t('vfs.download', 'Download')}
            </ContextMenuItem>
          )}
          {capabilities.includes('copy_path') && (
            <ContextMenuItem onSelect={() => void onCopyPath()}>
              <Copy className="mr-2 h-4 w-4" />
              {t('vfs.copy_path', 'Copy Path')}
            </ContextMenuItem>
          )}
          {(capabilities.includes('rename') || capabilities.includes('delete')) && (
            <>
              <ContextMenuSeparator />
              {capabilities.includes('rename') && (
                <ContextMenuItem onSelect={() => { setDraftName(name); setRenameOpen(true); }}>
                  <Pencil className="mr-2 h-4 w-4" />
                  {t('vfs.rename', 'Rename')}
                </ContextMenuItem>
              )}
              {capabilities.includes('delete') && (
                <ContextMenuItem className="text-destructive focus:text-destructive" onSelect={() => setDeleteOpen(true)}>
                  <Trash2 className="mr-2 h-4 w-4" />
                  {t('vfs.delete', 'Delete')}
                </ContextMenuItem>
              )}
            </>
          )}
        </ContextMenuContent>
      </ContextMenu>

      {uploadFolder && (
        <input
          ref={uploadInputRef}
          type="file"
          className="hidden"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) onPickUpload(f);
            e.target.value = '';
          }}
        />
      )}

      <Dialog open={renameOpen} onOpenChange={setRenameOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t('vfs.rename', 'Rename')}</DialogTitle>
            <DialogDescription className="font-mono text-xs">{path}</DialogDescription>
          </DialogHeader>
          <Input
            value={draftName}
            autoFocus
            onChange={(e) => setDraftName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') submitRename();
            }}
          />
          <DialogFooter>
            <Button variant="outline" onClick={() => setRenameOpen(false)}>
              {t('cancel', 'Cancel')}
            </Button>
            <Button onClick={submitRename} disabled={rename.isPending}>
              {t('vfs.rename', 'Rename')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={pendingOverwrite !== null}
        onOpenChange={(open) => {
          if (!open && !upload.isPending) setPendingOverwrite(null);
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t('vfs.upload_replace_title', 'Replace existing file?')}</DialogTitle>
            <DialogDescription>
              {t('vfs.upload_confirm_replace', {
                name: pendingOverwrite?.name ?? '',
                folder: uploadFolder ? `/${uploadFolder}` : '',
                defaultValue: 'A file named "{{name}}" already exists in {{folder}}. Replacing it cannot be undone.',
              })}
            </DialogDescription>
          </DialogHeader>
          {pendingOverwrite ? (
            <div className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1 rounded-md bg-surface-sunken px-3 py-2 text-sm">
              <span className="text-muted-foreground">{t('vfs.file', 'File')}</span>
              <span className="truncate font-medium">{pendingOverwrite.name}</span>
              <span className="text-muted-foreground">{t('vfs.destination', 'Destination')}</span>
              <span className="truncate font-mono text-xs">/{uploadFolder}</span>
              <span className="text-muted-foreground">{t('vfs.size', 'Size')}</span>
              <span>{formatBytes(pendingOverwrite.size)}</span>
            </div>
          ) : null}
          <DialogFooter>
            <Button variant="outline" onClick={() => setPendingOverwrite(null)} disabled={upload.isPending}>
              {t('cancel', 'Cancel')}
            </Button>
            <Button
              variant="danger"
              disabled={!pendingOverwrite || upload.isPending}
              onClick={() => pendingOverwrite && uploadFile(pendingOverwrite)}
            >
              {upload.isPending ? t('vfs.uploading', 'Uploading…') : t('vfs.replace', 'Replace')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={deleteOpen} onOpenChange={setDeleteOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t('vfs.delete', 'Delete')}</DialogTitle>
            <DialogDescription>
              {t('vfs.delete_confirm', 'Permanently delete "{{name}}"? This cannot be undone.', { name })}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteOpen(false)}>
              {t('cancel', 'Cancel')}
            </Button>
            <Button variant="destructive" onClick={submitDelete} disabled={del.isPending}>
              {t('vfs.delete', 'Delete')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
