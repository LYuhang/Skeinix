/**
 * Inspector → Info tab — read-only workflow metadata.
 *
 * Reads the snapshot via `useWorkflow(wfId)`. CanvasPage has already
 * primed this query before mounting the inspector, so the cache hit
 * is instant on first render; we still gate on `data` so the tab
 * stays empty during the (effectively-zero) loading window rather
 * than flashing partial values.
 *
 * The version label mirrors the engine's `v{n}_sv{m}` convention
 * (legacy: `WorkflowVersionTree`) — we render the dotted UI form
 * `v{active_v}.sv{active_sv}` here purely for readability. Created /
 * Updated come back as POSIX seconds; `* 1000` is the standard
 * conversion to JS milliseconds.
 */
import { useTranslation } from 'react-i18next';
import { useWorkflow } from '@/lib/api/queries/workflow';
import { useFormatDateTime } from '@/lib/timezone';

export function InfoTab({ wfId }: { wfId: string }) {
  const { t } = useTranslation();
  const { data } = useWorkflow(wfId);
  // Created/Updated are POSIX seconds; render in the user's chosen timezone.
  const formatDateTime = useFormatDateTime();
  if (!data) return null;
  const { meta } = data;

  return (
    <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-2 text-sm">
      <dt className="text-muted-foreground">{t('inspector.info.name', 'Name')}</dt>
      <dd className="font-medium">{meta.workflow_name}</dd>

      <dt className="text-muted-foreground">
        {t('inspector.info.description', 'Description')}
      </dt>
      <dd className="whitespace-pre-wrap break-words">
        {meta.description || (
          <span className="text-muted-foreground">—</span>
        )}
      </dd>

      <dt className="text-muted-foreground">
        {t('inspector.info.version', 'Version')}
      </dt>
      <dd className="font-mono text-xs">
        v{meta.active_v}.sv{meta.active_sv}
      </dd>

      <dt className="text-muted-foreground">
        {t('inspector.info.created', 'Created')}
      </dt>
      <dd className="text-xs">{formatDateTime(meta.created_at)}</dd>

      <dt className="text-muted-foreground">
        {t('inspector.info.updated', 'Updated')}
      </dt>
      <dd className="text-xs">{formatDateTime(meta.updated_at)}</dd>
    </dl>
  );
}
