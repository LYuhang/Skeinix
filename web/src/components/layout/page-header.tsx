import type { ComponentType, ReactNode } from 'react';

import { ResourceIcon } from '@/components/presentation/ResourceIcon';
import type { ResourceKind } from '@/lib/presentation/resource-visuals';
import { cn } from '@/lib/utils';

export function PageHeader({
  title,
  description,
  icon: Icon,
  resourceKind,
  status,
  actions,
  metadata,
  eyebrow,
  className,
}: {
  title: ReactNode;
  description?: ReactNode;
  icon?: ComponentType<{ className?: string; 'aria-hidden'?: boolean }>;
  resourceKind?: ResourceKind;
  status?: ReactNode;
  actions?: ReactNode;
  metadata?: ReactNode;
  eyebrow?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn('flex flex-wrap items-start justify-between gap-4', className)}>
      <div className="min-w-0 flex-1">
        {eyebrow ? (
          <div className="mb-1 text-xs font-medium uppercase tracking-[0.1em] text-content-tertiary">
            {eyebrow}
          </div>
        ) : null}
        <div className="flex min-w-0 items-center gap-2.5">
          {resourceKind ? <ResourceIcon kind={resourceKind} size="md" /> : null}
          {!resourceKind && Icon ? (
            <Icon className="size-5 shrink-0 text-content-secondary" aria-hidden />
          ) : null}
          <h1 className="text-title min-w-0 truncate text-content-primary">{title}</h1>
          {status}
        </div>
        {description ? (
          <p className="mt-1 max-w-3xl text-sm leading-5 text-content-secondary">{description}</p>
        ) : null}
        {metadata ? (
          <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-content-tertiary">
            {metadata}
          </div>
        ) : null}
      </div>
      {actions ? <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div> : null}
    </div>
  );
}
