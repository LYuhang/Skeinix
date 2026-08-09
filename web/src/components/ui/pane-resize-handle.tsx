import { useState } from 'react';
import type { KeyboardEvent, PointerEvent as ReactPointerEvent } from 'react';

import { cn } from '@/lib/utils';

export interface PaneResizeHandleProps {
  side: 'left' | 'right';
  width: number;
  minWidth: number;
  maxWidth: number;
  onWidthChange: (width: number) => void;
  onReset: () => void;
  label: string;
  className?: string;
  dataAction?: string;
}

export function PaneResizeHandle({
  side,
  width,
  minWidth,
  maxWidth,
  onWidthChange,
  onReset,
  label,
  className,
  dataAction,
}: PaneResizeHandleProps) {
  const [dragging, setDragging] = useState(false);

  const onPointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    event.preventDefault();
    const target = event.currentTarget;
    const pointerId = event.pointerId;
    const startX = event.clientX;
    const startWidth = width;
    let pendingWidth = startWidth;
    let animationFrame: number | null = null;
    target.setPointerCapture(pointerId);
    setDragging(true);

    const onMove = (moveEvent: PointerEvent) => {
      const delta = moveEvent.clientX - startX;
      pendingWidth = startWidth + (side === 'right' ? delta : -delta);
      if (animationFrame !== null) return;
      animationFrame = window.requestAnimationFrame(() => {
        animationFrame = null;
        onWidthChange(pendingWidth);
      });
    };
    const onEnd = () => {
      if (animationFrame !== null) {
        window.cancelAnimationFrame(animationFrame);
        animationFrame = null;
        onWidthChange(pendingWidth);
      }
      target.releasePointerCapture(pointerId);
      target.removeEventListener('pointermove', onMove);
      target.removeEventListener('pointerup', onEnd);
      target.removeEventListener('pointercancel', onEnd);
      setDragging(false);
    };
    target.addEventListener('pointermove', onMove);
    target.addEventListener('pointerup', onEnd);
    target.addEventListener('pointercancel', onEnd);
  };

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const visualDirection = side === 'right' ? 1 : -1;
    if (event.key === 'ArrowRight') {
      event.preventDefault();
      onWidthChange(width + 16 * visualDirection);
    } else if (event.key === 'ArrowLeft') {
      event.preventDefault();
      onWidthChange(width - 16 * visualDirection);
    } else if (event.key === 'Home') {
      event.preventDefault();
      onWidthChange(minWidth);
    } else if (event.key === 'End') {
      event.preventDefault();
      onWidthChange(maxWidth);
    }
  };

  return (
    <div
      role="separator"
      tabIndex={0}
      aria-label={label}
      aria-orientation="vertical"
      aria-valuemin={minWidth}
      aria-valuemax={maxWidth}
      aria-valuenow={Math.round(width)}
      title={label}
      data-action={dataAction}
      data-resizing={dragging || undefined}
      className={cn(
        'group absolute inset-y-0 z-sticky w-2 cursor-col-resize touch-none outline-none',
        side === 'right' ? '-right-1' : '-left-1',
        className,
      )}
      onPointerDown={onPointerDown}
      onDoubleClick={onReset}
      onKeyDown={onKeyDown}
    >
      <span className="absolute inset-y-0 left-1/2 w-px -translate-x-1/2 bg-edge-structural transition-colors duration-feedback group-hover:bg-focus group-focus-visible:w-0.5 group-focus-visible:bg-focus group-data-[resizing]:w-0.5 group-data-[resizing]:bg-focus" />
    </div>
  );
}
