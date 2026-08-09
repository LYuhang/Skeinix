import { CircleAlert, RotateCw } from 'lucide-react';
import type { ReactNode } from 'react';

import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';

export function ActionableError({
  title,
  description,
  actionLabel,
  onAction,
  technicalDetails,
  technicalDetailsLabel = 'Technical details',
  requestId,
  className,
}: {
  title: ReactNode;
  description?: ReactNode;
  actionLabel?: ReactNode;
  onAction?: () => void;
  technicalDetails?: ReactNode;
  technicalDetailsLabel?: ReactNode;
  requestId?: string | null;
  className?: string;
}) {
  return (
    <section
      role="alert"
      className={cn('rounded-lg border border-state-danger/30 bg-state-danger/5 p-4', className)}
    >
      <div className="flex items-start gap-3">
        <span className="grid size-9 shrink-0 place-items-center rounded-lg bg-state-danger/10 text-state-danger">
          <CircleAlert className="size-4" aria-hidden="true" />
        </span>
        <div className="min-w-0 flex-1">
          <h3 className="text-sm font-semibold text-content-primary">{title}</h3>
          {description ? <p className="mt-1 text-sm leading-5 text-content-secondary">{description}</p> : null}
          {actionLabel && onAction ? (
            <Button type="button" variant="outline" size="sm" className="mt-3" onClick={onAction}>
              <RotateCw className="size-3.5" aria-hidden="true" />
              {actionLabel}
            </Button>
          ) : null}
          {technicalDetails || requestId ? (
            <details className="mt-3 text-xs text-content-tertiary">
              <summary className="w-fit cursor-pointer rounded-sm font-medium text-content-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus">
                {technicalDetailsLabel}
              </summary>
              <div className="mt-2 max-h-48 overflow-auto rounded-md bg-surface-sunken p-3 font-mono leading-5">
                {technicalDetails}
                {requestId ? <div>request_id: {requestId}</div> : null}
              </div>
            </details>
          ) : null}
        </div>
      </div>
    </section>
  );
}
