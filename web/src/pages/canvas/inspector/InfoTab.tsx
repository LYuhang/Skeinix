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
import { DetailSummary } from '@/components/layout/detail-summary';
import { SectionBlock } from '@/components/layout/section-block';
import { ResourceProvenanceLine } from '@/components/resources/ResourceProvenanceLine';

export function InfoTab({ wfId }: { wfId: string }) {
  const { t } = useTranslation();
  const { data } = useWorkflow(wfId);
  // Created/Updated are POSIX seconds; render in the user's chosen timezone.
  const formatDateTime = useFormatDateTime();
  if (!data) return null;
  const { meta } = data;

  return (
    <SectionBlock title={t('inspector.info.summary', 'Workflow details')}>
      <DetailSummary
        className="grid-cols-1 sm:grid-cols-1"
        items={[
          { label: t('inspector.info.name', 'Name'), value: meta.workflow_name },
          { label: t('inspector.info.version', 'Version'), value: <code>v{meta.active_v}.sv{meta.active_sv}</code> },
          { label: t('inspector.info.created', 'Created'), value: formatDateTime(meta.created_at) },
          { label: t('inspector.info.updated', 'Updated'), value: formatDateTime(meta.updated_at) },
          { label: t('inspector.info.provenance', 'Source'), value: <ResourceProvenanceLine provenance={meta.provenance} /> },
          { label: t('inspector.info.description', 'Description'), value: meta.description || '—', wide: true },
        ]}
      />
    </SectionBlock>
  );
}
