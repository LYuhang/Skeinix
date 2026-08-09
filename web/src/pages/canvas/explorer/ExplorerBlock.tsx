import { useEffect, useState, type ReactNode } from 'react';
import { ChevronDown } from 'lucide-react';

export interface ExplorerBlockProps {
  /** Stable id for the block (debug / keying). Not required to be unique in DOM. */
  id?: string;
  /** Prominent, already-translated block title. */
  title: string;
  /** Block body — the section component. */
  children: ReactNode;
  /** Whether the block starts collapsed. Defaults to collapsed (true). */
  defaultCollapsed?: boolean;
  /** Per-workflow persistence key. The Explorer body remains the only scroll owner. */
  persistenceKey?: string;
}

/**
 * Collapsible block wrapper for the left Explorer rail. Each section
 * (Versions / Nodes / sandboxes / agent workflows) is framed by one
 * of these: a prominent BOLD header row that is the full click target (toggles
 * open/closed, keyboard-accessible via a real <button> + aria-expanded), a
 * chevron on the RIGHT that rotates on toggle and a divider between sections.
 */
export function ExplorerBlock({
  id,
  title,
  children,
  defaultCollapsed = true,
  persistenceKey,
}: ExplorerBlockProps) {
  const [open, setOpen] = useState(() => {
    if (!persistenceKey) return !defaultCollapsed;
    try {
      const stored = localStorage.getItem(persistenceKey);
      return stored == null ? !defaultCollapsed : stored === 'open';
    } catch {
      return !defaultCollapsed;
    }
  });

  useEffect(() => {
    if (!persistenceKey) return;
    try { localStorage.setItem(persistenceKey, open ? 'open' : 'closed'); } catch { /* optional UI state */ }
  }, [open, persistenceKey]);

  return (
    <section
      data-block-id={id}
      className="border-b border-edge-structural/70 last:border-b-0"
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className={`flex w-full items-center justify-between gap-2 px-3 py-2.5 text-left transition-colors duration-feedback hover:bg-surface-hover hover:text-content-primary ${
          open
            ? 'bg-surface-sunken/70 text-content-primary'
            : 'bg-surface-raised/55 text-content-secondary'
        }`}
      >
        <span className="text-[13px] font-semibold">
          {title}
        </span>
        <ChevronDown
          className={`h-3.5 w-3.5 shrink-0 text-muted-foreground transition-transform duration-feedback ${
            open ? 'rotate-0' : '-rotate-90'
          }`}
        />
      </button>
      {open && (
        <div
          className="border-t border-edge-subtle bg-surface-raised/80 py-1.5"
          data-role="explorer-block-content"
        >
          {children}
        </div>
      )}
    </section>
  );
}
