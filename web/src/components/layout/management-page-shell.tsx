import type { ComponentType, ReactNode } from 'react';
import { PageHeader } from '@/components/layout/page-header';
import type { ResourceKind } from '@/lib/presentation/resource-visuals';
import { cn } from '@/lib/utils';

export function ManagementPageShell({
  title,
  description,
  icon: Icon,
  resourceKind,
  actions,
  contained = true,
  children,
  className,
}: {
  title: ReactNode;
  description?: ReactNode;
  icon?: ComponentType<{ className?: string; 'aria-hidden'?: boolean }>;
  resourceKind?: ResourceKind;
  actions?: ReactNode;
  contained?: boolean;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn('page-shell', contained && 'page-shell-contained')}
      data-page-archetype="list-index"
    >
      <div className={cn('page-content management-page-content', className)}>
        <header className="shrink-0" data-page-region="header">
          <PageHeader
            title={title}
            description={description}
            icon={Icon}
            resourceKind={resourceKind}
            actions={actions}
          />
        </header>
        {children}
      </div>
    </div>
  );
}

export function ManagementToolbar({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div
      className={cn('flex shrink-0 flex-wrap items-center gap-3 border-y border-edge-subtle bg-surface-sunken/45 px-3 py-2.5', className)}
      data-page-region="toolbar"
    >
      {children}
    </div>
  );
}
