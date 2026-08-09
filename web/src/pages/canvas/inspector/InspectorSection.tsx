import { useId, useState, type ReactNode } from 'react';
import type { LucideIcon } from 'lucide-react';
import { ChevronDown } from 'lucide-react';

interface InspectorSectionProps {
  title: string;
  icon: LucideIcon;
  children: ReactNode;
  actions?: ReactNode;
  testId?: string;
  defaultOpen?: boolean;
}

/**
 * Shared visual boundary for one Inspector responsibility.
 *
 * The tinted header establishes where a block begins; the header border and
 * the outer card border make its extent unambiguous even in a long, dense node
 * form. Keep the body neutral so field controls remain the visual focus.
 */
export function InspectorSection({
  title,
  icon: Icon,
  children,
  actions,
  testId,
  defaultOpen = true,
}: InspectorSectionProps) {
  const [open, setOpen] = useState(defaultOpen);
  const contentId = useId();

  return (
    <section
      className="overflow-hidden rounded-lg border border-edge-subtle bg-background shadow-[0_1px_2px_rgba(15,23,42,0.04)]"
      data-testid={testId}
    >
      <header
        className={`flex min-h-10 items-center gap-2 bg-muted/55 px-2 py-1.5${
          open ? ' border-b border-edge-subtle' : ''
        }`}
      >
        <button
          type="button"
          className="flex min-w-0 flex-1 items-center gap-2 rounded-md px-1 py-0.5 text-left outline-none hover:bg-background/65 focus-visible:ring-2 focus-visible:ring-focus/40"
          aria-expanded={open}
          aria-controls={contentId}
          aria-label={`${open ? 'Collapse' : 'Expand'} ${title}`}
          onClick={() => setOpen((value) => !value)}
        >
          <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md border border-edge-subtle bg-background text-content-secondary">
            <Icon className="h-3.5 w-3.5" aria-hidden="true" />
          </span>
          <h3 className="min-w-0 flex-1 truncate text-[13px] font-semibold text-foreground">
            {title}
          </h3>
          <ChevronDown
            className={`h-4 w-4 shrink-0 text-content-tertiary transition-transform${
              open ? '' : ' -rotate-90'
            }`}
            aria-hidden="true"
          />
        </button>
        {actions && <div className="flex shrink-0 items-center gap-1.5">{actions}</div>}
      </header>
      <div id={contentId} className="px-3 py-3" hidden={!open}>
        {children}
      </div>
    </section>
  );
}
