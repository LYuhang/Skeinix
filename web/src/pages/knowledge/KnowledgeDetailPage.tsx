import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  BookOpen,
  Pencil,
  RefreshCw,
  Share2,
  Trash2,
} from 'lucide-react';
import { useNavigate, useParams } from 'react-router';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import { EntityDetailShell } from '@/components/layout/entity-detail-shell';
import { ResourceShareDialog } from '@/components/modals/ResourceShareDialog';
import { ResourceProvenanceLine } from '@/components/resources/ResourceProvenanceLine';
import { AsyncState } from '@/components/ui/async-state';
import { Button } from '@/components/ui/button';
import { ConfirmationDialog } from '@/components/ui/confirmation-dialog';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import {
  deleteKb,
  deleteKbFile,
  getKb,
  listKbFiles,
  updateKb,
  uploadKbFile,
  type KbFile,
} from '@/lib/api/kb';
import { KnowledgeSourceExplorer } from '@/pages/knowledge/KnowledgeSourceExplorer';

function errorState(error: unknown): 'permission' | 'error' {
  const message = error instanceof Error ? error.message : String(error ?? '');
  return /(?:\b403\b|forbidden|permission)/i.test(message) ? 'permission' : 'error';
}

export function KnowledgeDetailPage() {
  const { kbId = '' } = useParams<{ kbId: string }>();
  const navigate = useNavigate();
  const { t } = useTranslation();
  const client = useQueryClient();
  const [shareOpen, setShareOpen] = useState(false);
  const [editOpen, setEditOpen] = useState(false);
  const [editName, setEditName] = useState('');
  const [editDescription, setEditDescription] = useState('');
  const [deleteKbOpen, setDeleteKbOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<{
    files: KbFile[];
    label: string;
    folder: boolean;
  } | null>(null);

  const detail = useQuery({
    queryKey: ['knowledge-base', kbId],
    queryFn: () => getKb(kbId),
    enabled: Boolean(kbId),
  });
  const files = useQuery({
    queryKey: ['knowledge-files', kbId],
    queryFn: () => listKbFiles(kbId),
    enabled: Boolean(kbId),
  });

  const refresh = async () => Promise.all([detail.refetch(), files.refetch()]);
  const upload = useMutation({
    mutationFn: async (items: Array<{ file: File; path: string }>) => {
      for (const item of items) await uploadKbFile(kbId, item.file, item.path);
      return items.length;
    },
    onSuccess: async (count) => {
      await Promise.all([
        client.invalidateQueries({ queryKey: ['knowledge-base', kbId] }),
        client.invalidateQueries({ queryKey: ['knowledge-files', kbId] }),
      ]);
      toast.success(t('knowledge.filesUploaded', '{{count}} files uploaded', { count }));
    },
    onError: async (reason) => {
      await Promise.all([
        client.invalidateQueries({ queryKey: ['knowledge-base', kbId] }),
        client.invalidateQueries({ queryKey: ['knowledge-files', kbId] }),
      ]);
      toast.error(reason instanceof Error ? reason.message : String(reason));
    },
  });
  const removeFile = useMutation({
    mutationFn: async (target: { files: KbFile[] }) => {
      for (const file of target.files) await deleteKbFile(kbId, file.id);
      return target.files.length;
    },
    onSuccess: async (count) => {
      await Promise.all([
        client.invalidateQueries({ queryKey: ['knowledge-base', kbId] }),
        client.invalidateQueries({ queryKey: ['knowledge-files', kbId] }),
      ]);
      setDeleteTarget(null);
      toast.success(t('knowledge.filesDeleted', '{{count}} files deleted', { count }));
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
  const editMetadata = useMutation({
    mutationFn: () => updateKb(kbId, {
      name: editName.trim(),
      description: editDescription.trim(),
    }),
    onSuccess: async () => {
      await Promise.all([
        client.invalidateQueries({ queryKey: ['knowledge-base', kbId] }),
        client.invalidateQueries({ queryKey: ['knowledge-bases'] }),
      ]);
      setEditOpen(false);
      toast.success(t('knowledge.updated', 'Knowledge details updated'));
    },
    onError: (reason) => toast.error(reason instanceof Error ? reason.message : String(reason)),
  });

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

  const canUpdate = detail.data.access.capabilities.includes('update');
  const canShare = detail.data.access.capabilities.includes('manage_access');
  const canDelete = detail.data.access.capabilities.includes('delete');

  return (
    <>
      <EntityDetailShell
        resourceKind="knowledge"
        backTo="/knowledge"
        backLabel={t('knowledge.back', 'Knowledge')}
        title={detail.data.name}
        description={detail.data.description || t('knowledge.noDescription', 'No description')}
        icon={BookOpen}
        metadata={(
          <>
            <span>{detail.data.file_count} {t('knowledge.files', 'files')}</span>
            <span>{t('knowledge.version', 'Version {{version}}', { version: detail.data.package_version })}</span>
            <ResourceProvenanceLine provenance={detail.data.provenance} />
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
            {canUpdate ? (
              <Button
                variant="outline"
                size="sm"
                onClick={() => {
                  setEditName(detail.data.name);
                  setEditDescription(detail.data.description ?? '');
                  setEditOpen(true);
                }}
              >
                <Pencil className="h-4 w-4" />{t('edit', 'Edit')}
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
          </>
        )}
      >
        <section className="min-h-0 flex-1" aria-label={t('knowledge.sourceFiles', 'Files')}>
          {files.isLoading ? <AsyncState kind="loading" title={t('knowledge.loadingFiles', 'Loading files…')} /> : null}
          {files.isError ? (
            <AsyncState
              kind={errorState(files.error)}
              title={errorState(files.error) === 'permission'
                ? t('knowledge.filesForbidden', 'You do not have access to these files')
                : t('knowledge.filesFailed', 'Could not load files')}
              actionLabel={errorState(files.error) === 'error' ? t('retry', 'Retry') : undefined}
              onAction={errorState(files.error) === 'error' ? () => void files.refetch() : undefined}
            />
          ) : null}
          {!files.isLoading && !files.isError && !(files.data?.length) ? (
            <AsyncState
              kind="empty"
              title={t('knowledge.noFiles', 'No files')}
              description={t('knowledge.noFilesHint', 'Upload a file to add it to this knowledge folder.')}
            />
          ) : null}
          {files.data?.length ? (
            <KnowledgeSourceExplorer
              kbId={kbId}
              files={files.data}
              canUpdate={canUpdate}
              uploading={upload.isPending}
              deleting={removeFile.isPending}
              onUpload={(items) => upload.mutate(items)}
              onDeleteFiles={(targetFiles, folderPath) => setDeleteTarget({ files: targetFiles, label: folderPath, folder: true })}
              onDelete={(file) => setDeleteTarget({ files: [file], label: file.name, folder: false })}
            />
          ) : null}
        </section>

        <ConfirmationDialog
          open={deleteKbOpen}
          onOpenChange={(open) => {
            if (!removeKnowledgeBase.isPending) setDeleteKbOpen(open);
          }}
          title={t('knowledge.deleteTitle', 'Delete this knowledge base?')}
          description={t(
            'knowledge.deleteDescription',
            '{{name}} and all files in this knowledge folder will be permanently deleted.',
            { name: detail.data.name },
          )}
          confirmLabel={removeKnowledgeBase.isPending ? t('deleting', 'Deleting…') : t('delete', 'Delete')}
          cancelLabel={t('cancel', 'Cancel')}
          pending={removeKnowledgeBase.isPending}
          onConfirm={() => removeKnowledgeBase.mutate()}
        />

        <Dialog open={editOpen} onOpenChange={setEditOpen}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>{t('knowledge.editTitle', 'Edit Knowledge details')}</DialogTitle>
              <DialogDescription>{t('knowledge.editHint', 'Keep the title and description concise so people and Agents can recognize the package.')}</DialogDescription>
            </DialogHeader>
            <div className="space-y-4">
              <div className="space-y-1.5">
                <Label htmlFor="knowledge-edit-name">{t('name', 'Name')}</Label>
                <Input id="knowledge-edit-name" value={editName} onChange={(event) => setEditName(event.target.value)} />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="knowledge-edit-description">{t('description', 'Description')}</Label>
                <Textarea id="knowledge-edit-description" value={editDescription} onChange={(event) => setEditDescription(event.target.value)} />
              </div>
            </div>
            <DialogFooter>
              <Button variant="outline" onClick={() => setEditOpen(false)}>{t('cancel', 'Cancel')}</Button>
              <Button disabled={!editName.trim() || editMetadata.isPending} onClick={() => editMetadata.mutate()}>
                {editMetadata.isPending ? t('saving', 'Saving…') : t('save', 'Save')}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>

        <ConfirmationDialog
          open={deleteTarget !== null}
          onOpenChange={(open) => {
            if (!open && !removeFile.isPending) setDeleteTarget(null);
          }}
          title={deleteTarget?.folder ? t('knowledge.deleteFolderTitle', 'Delete folder?') : t('knowledge.deleteFileTitle', 'Delete file?')}
          description={deleteTarget?.folder
            ? t('knowledge.deleteFolderDescription', '{{name}} and its {{count}} files will be permanently deleted.', { name: deleteTarget.label, count: deleteTarget.files.length })
            : t('knowledge.deleteFileDescription', '{{name}} will be permanently deleted from this knowledge folder.', { name: deleteTarget?.label ?? '' })}
          confirmLabel={removeFile.isPending ? t('deleting', 'Deleting…') : t('delete', 'Delete')}
          cancelLabel={t('cancel', 'Cancel')}
          pending={removeFile.isPending}
          onConfirm={() => {
            if (deleteTarget) removeFile.mutate(deleteTarget);
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
