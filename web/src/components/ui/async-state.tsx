import type { ReactNode } from 'react';
import {
  Ban,
  CheckCircle2,
  CircleAlert,
  Inbox,
  LoaderCircle,
  ShieldAlert,
  TriangleAlert,
  type LucideIcon,
} from 'lucide-react';

import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';

export type AsyncStateKind =
  | 'loading'
  | 'empty'
  | 'error'
  | 'partial'
  | 'permission'
  | 'disabled'
  | 'success';

const stateIcon: Record<AsyncStateKind, LucideIcon> = {
  loading: LoaderCircle,
  empty: Inbox,
  error: CircleAlert,
  partial: TriangleAlert,
  permission: ShieldAlert,
  disabled: Ban,
  success: CheckCircle2,
};

export function AsyncState({
  kind,
  title,
  description,
  actionLabel,
  onAction,
  technicalDetails,
  technicalDetailsLabel = 'Technical details',
  className,
}: {
  kind: AsyncStateKind;
  title: ReactNode;
  description?: ReactNode;
  actionLabel?: ReactNode;
  onAction?: () => void;
  technicalDetails?: ReactNode;
  technicalDetailsLabel?: ReactNode;
  className?: string;
}) {
  const Icon = stateIcon[kind];
  return (
    <div
      className={cn(
        'flex min-h-40 flex-col items-center justify-center rounded-lg border border-transparent bg-surface-sunken/45 px-6 py-8 text-center',
        kind === 'error' && 'border-state-danger/35 bg-state-danger/5',
        kind === 'permission' && 'border-state-danger/25 bg-state-danger/5',
        className,
      )}
      role={kind === 'error' || kind === 'permission' ? 'alert' : 'status'}
      aria-live={kind === 'loading' ? 'polite' : undefined}
    >
      <Icon
        className={cn(
          'mb-3 h-6 w-6 text-content-tertiary',
          kind === 'loading' && 'motion-safe:animate-spin',
          kind === 'error' && 'text-state-danger',
          kind === 'permission' && 'text-state-danger',
          kind === 'partial' && 'text-state-warning',
          kind === 'success' && 'text-state-success',
        )}
        aria-hidden="true"
      />
      <div className="text-sm font-medium text-content-primary">{title}</div>
      {description ? <div className="mt-1 max-w-lg text-sm text-content-secondary">{description}</div> : null}
      {technicalDetails ? (
        <details className="mt-3 max-w-full text-left text-xs text-content-tertiary">
          <summary className="cursor-pointer select-none text-center font-medium hover:text-content-secondary">
            {technicalDetailsLabel}
          </summary>
          <pre className="mt-2 max-h-40 max-w-xl overflow-auto whitespace-pre-wrap break-words rounded-md bg-surface-sunken px-3 py-2 font-mono text-xs leading-5 text-content-secondary">
            {technicalDetails}
          </pre>
        </details>
      ) : null}
      {actionLabel && onAction ? (
        <Button variant="outline" size="sm" className="mt-4" onClick={onAction}>
          {actionLabel}
        </Button>
      ) : null}
    </div>
  );
}
