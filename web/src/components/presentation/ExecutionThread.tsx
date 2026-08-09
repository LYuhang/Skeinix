import {
  CheckCircle2,
  Circle,
  CircleAlert,
  CircleX,
  LoaderCircle,
} from 'lucide-react';
import type { ReactNode } from 'react';

import type { SemanticStatus } from '@/components/ui/status';
import { cn } from '@/lib/utils';

export interface ExecutionThreadItem {
  id: string;
  title: ReactNode;
  detail?: ReactNode;
  meta?: ReactNode;
  content?: ReactNode;
  status?: SemanticStatus;
  dataRole?: string;
  dataStatus?: string;
}

const statusVisuals = {
  neutral: { icon: Circle, color: 'text-content-tertiary' },
  info: { icon: Circle, color: 'text-state-info' },
  running: { icon: LoaderCircle, color: 'text-state-running' },
  success: { icon: CheckCircle2, color: 'text-state-success' },
  warning: { icon: CircleAlert, color: 'text-state-warning' },
  danger: { icon: CircleX, color: 'text-state-danger' },
} as const;

export function ExecutionThread({
  items,
  className,
}: {
  items: readonly ExecutionThreadItem[];
  className?: string;
}) {
  return (
    <ol className={cn('space-y-0', className)}>
      {items.map((item, index) => {
        const status = item.status ?? 'neutral';
        const visual = statusVisuals[status];
        const Icon = visual.icon;
        const last = index === items.length - 1;
        return (
          <li
            key={item.id}
            className="relative grid grid-cols-[1.5rem_minmax(0,1fr)] gap-2.5 pb-4 last:pb-0"
            data-role={item.dataRole}
            data-phase-status={item.dataStatus}
          >
            {!last ? (
              <span className="absolute bottom-0 left-[0.71875rem] top-5 w-px bg-edge-structural" aria-hidden="true" />
            ) : null}
            <span className={cn('relative z-[1] mt-0.5 grid size-6 place-items-center rounded-full bg-surface-work', visual.color)}>
              <Icon
                className={cn('size-4', status === 'running' && 'motion-safe:animate-spin')}
                aria-hidden="true"
                data-glyph={status === 'success' ? 'done' : status === 'running' ? 'running' : status === 'danger' ? 'error' : 'pending'}
              />
            </span>
            <div className="min-w-0 pt-0.5">
              <div className="flex min-h-5 items-start justify-between gap-3 text-sm">
                <span className="min-w-0 font-medium text-content-primary">{item.title}</span>
                {item.meta ? <span className="shrink-0 text-xs text-content-tertiary">{item.meta}</span> : null}
              </div>
              {item.detail ? <div className="mt-0.5 text-xs leading-5 text-content-secondary">{item.detail}</div> : null}
              {item.content ? <div className="mt-2">{item.content}</div> : null}
            </div>
          </li>
        );
      })}
    </ol>
  );
}
