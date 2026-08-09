import { useEffect, useId, useRef, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { X } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { PaneResizeHandle } from '@/components/ui/pane-resize-handle';
import { usePersistedPaneWidth } from '@/components/ui/use-persisted-pane-width';

export function AuxiliaryPane({
  open,
  title,
  closeLabel,
  resizeLabel,
  storageKey,
  children,
  onClose,
}: {
  open: boolean;
  title: ReactNode;
  closeLabel: string;
  resizeLabel: string;
  storageKey: string;
  children: ReactNode;
  onClose: () => void;
}) {
  const triggerRef = useRef<HTMLElement | null>(null);
  const onCloseRef = useRef(onClose);
  const titleId = useId();
  const pane = usePersistedPaneWidth({
    storageKey,
    defaultWidth: 640,
    minWidth: 480,
    maxWidth: 960,
  });

  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    if (!open) return;
    triggerRef.current = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      event.preventDefault();
      onCloseRef.current();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => {
      window.removeEventListener('keydown', onKeyDown);
      requestAnimationFrame(() => triggerRef.current?.focus());
    };
  }, [open]);

  if (!open || typeof document === 'undefined') return null;
  return createPortal(
    <aside
      role="region"
      aria-labelledby={titleId}
      className="pane-enter-from-right fixed bottom-0 right-0 top-[6.5rem] z-auxiliary flex max-w-[calc(100vw-15rem)] flex-col bg-surface-view shadow-raised"
      style={{ width: pane.width }}
    >
      <PaneResizeHandle
        side="left"
        width={pane.width}
        minWidth={480}
        maxWidth={960}
        onWidthChange={pane.setWidth}
        onReset={pane.resetWidth}
        label={resizeLabel}
      />
      <header className="flex h-12 shrink-0 items-center gap-3 border-b border-edge-subtle px-3">
        <div id={titleId} className="min-w-0 flex-1 truncate text-sm font-semibold">{title}</div>
        <Button variant="quiet" size="icon-sm" onClick={onClose} aria-label={closeLabel}>
          <X className="h-4 w-4" />
        </Button>
      </header>
      <div className="app-scrollbar min-h-0 flex-1 overflow-auto p-4">{children}</div>
    </aside>,
    document.body,
  );
}
