import { useTranslation } from 'react-i18next';

import { AsyncState } from '@/components/ui/async-state';
import { StatusBadge } from '@/components/ui/status';
import type { VfsReadOut } from '@/lib/api/vfs';
import { formatBytes } from '@/lib/format/bytes';
import { resolveFileCapability } from '@/lib/files/capabilities';
import { renderVfsContent } from '@/pages/canvas/explorer/renderers';

export function VfsFilePreviewContent({
  data,
  loading,
  error,
  onRetry,
}: {
  data?: VfsReadOut;
  loading: boolean;
  error: boolean;
  onRetry: () => void;
}) {
  const { t } = useTranslation();
  if (loading) return <AsyncState kind="loading" title={t('vfs.loading', 'Loading…')} />;
  if (error || !data) {
    return (
      <AsyncState
        kind="error"
        title={t('vfs.load_error', 'Failed to load file.')}
        actionLabel={t('retry', 'Retry')}
        onAction={onRetry}
      />
    );
  }
  const capability = resolveFileCapability(data.path, data.content_type);
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        <StatusBadge status="neutral" showDot={false}>{capability.label}</StatusBadge>
        <span>{formatBytes(data.size_bytes)}</span>
        <span className="select-all font-mono">{data.path}</span>
      </div>
      {data.stale ? (
        <div className="border-y border-state-warning/30 bg-state-warning/10 px-3 py-2 text-xs text-state-warning">
          {t('vfs.stale', 'This file was produced at an earlier workflow version — it may be out of date; re-run to refresh.')}
        </div>
      ) : null}
      {data.truncated ? (
        <div className="border-y border-edge-subtle bg-surface-sunken px-3 py-2 text-xs text-muted-foreground">
          {t('vfs.truncated', 'Large file — content truncated for display.')}
        </div>
      ) : null}
      {renderVfsContent(data)}
    </div>
  );
}
