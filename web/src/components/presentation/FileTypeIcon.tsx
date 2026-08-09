import type { HTMLAttributes } from 'react';

import { fileVisualFor } from '@/lib/presentation/file-visuals';
import { cn } from '@/lib/utils';

export function FileTypeIcon({
  fileName,
  mimeType,
  directory,
  open,
  label,
  className,
  ...props
}: Omit<HTMLAttributes<HTMLSpanElement>, 'children'> & {
  fileName?: string | null;
  mimeType?: string | null;
  directory?: boolean;
  open?: boolean;
  label?: string;
}) {
  const visual = fileVisualFor({ fileName, mimeType, directory, open });
  const Icon = visual.icon;
  return (
    <span
      className={cn(
        'grid size-8 shrink-0 place-items-center rounded-lg',
        visual.foregroundClass,
        visual.surfaceClass,
        className,
      )}
      role={label ? 'img' : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
      data-file-kind={visual.kind}
      {...props}
    >
      <Icon className="size-4" />
    </span>
  );
}
