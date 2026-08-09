import { useTranslation } from 'react-i18next';
import { AuxiliaryPane } from '@/components/layout/auxiliary-pane';
import { useVfsContent } from '@/lib/api/queries/vfs';
import { VfsFilePreviewContent } from './VfsFilePreviewContent';

export interface VfsFileModalProps {
  wfId: string | undefined;
  path: string | null;
  onClose: () => void;
}

export function VfsFileModal({ wfId, path, onClose }: VfsFileModalProps) {
  const { t } = useTranslation();
  const q = useVfsContent(wfId, path);
  return (
    <AuxiliaryPane
      open={path !== null}
      title={<span className="block truncate font-mono">{path}</span>}
      closeLabel={t('close', 'Close')}
      resizeLabel={t('vfs.resize_preview', 'Resize file preview')}
      storageKey="vibecanvas:workflow-file-preview-width:v1"
      onClose={onClose}
    >
      <VfsFilePreviewContent data={q.data} loading={q.isLoading} error={q.isError} onRetry={() => void q.refetch()} />
    </AuxiliaryPane>
  );
}
