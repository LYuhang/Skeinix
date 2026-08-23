import { Building2, CircleUserRound, ShieldCheck } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import type { ResourceProvenance } from '@/lib/api/organizations';
import { cn } from '@/lib/utils';

const ORIGIN_KEYS: Record<
  ResourceProvenance['origin_type'],
  { key: string; fallback: string }
> = {
  created: { key: 'provenance.origin.created', fallback: 'Created' },
  uploaded: { key: 'provenance.origin.uploaded', fallback: 'Uploaded' },
  imported: { key: 'provenance.origin.imported', fallback: 'Imported' },
  catalog_install: {
    key: 'provenance.origin.catalogInstall',
    fallback: 'Catalog install',
  },
  derived: { key: 'provenance.origin.derived', fallback: 'Generated' },
  system: { key: 'provenance.origin.system', fallback: 'Built in' },
};

export function ResourceProvenanceLine({
  provenance,
  className,
}: {
  provenance: ResourceProvenance | null | undefined;
  className?: string;
}) {
  const { t } = useTranslation();
  // Keep the surrounding resource page usable during an atomic deployment
  // window if an older API response is still in a browser/query cache.
  if (!provenance) return null;
  const origin = ORIGIN_KEYS[provenance.origin_type];
  const originLabel = t(origin.key, origin.fallback);
  const scopeLabel = t(
    `provenance.scope.${provenance.ownership_scope}`,
    provenance.ownership_scope === 'personal'
      ? 'Personal'
      : provenance.ownership_scope === 'organization'
        ? 'Organization'
        : 'Platform',
  );
  const Icon = provenance.ownership_scope === 'personal'
    ? CircleUserRound
    : provenance.ownership_scope === 'organization'
      ? Building2
      : ShieldCheck;
  const creator = provenance.created_by?.display_name.trim();
  const owner = provenance.owner.display_name.trim();
  const creatorIsOwner = Boolean(
    creator
    && creator === owner
    && provenance.created_by?.type === provenance.owner.type,
  );

  return (
    <span
      className={cn(
        'inline-flex min-w-0 items-center gap-1.5 text-xs text-content-tertiary',
        className,
      )}
      title={`${scopeLabel}: ${owner}`}
    >
      <Icon className="size-3.5 shrink-0" aria-hidden="true" />
      <span className="truncate">{owner}</span>
      <span aria-hidden="true">·</span>
      <span className="shrink-0">{originLabel}</span>
      {creator && !creatorIsOwner ? (
        <>
          <span aria-hidden="true">·</span>
          <span className="truncate">
            {t('provenance.by', 'by {{name}}', { name: creator })}
          </span>
        </>
      ) : null}
      <span className="sr-only">{scopeLabel}</span>
    </span>
  );
}
