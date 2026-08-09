/**
 * Self-contained pagination control for the workspace list.
 *
 * There is no shared Pagination primitive in the UI kit, so this is a small
 * local widget wired to the page's client-side pagination state. Features:
 *   - Prev / Next buttons (disabled at the ends).
 *   - Numbered page buttons with ellipsis truncation for large counts
 *     (e.g. `1 … 4 5 6 … 20`).
 *   - A "jump to page" input (type a number + Enter → go).
 *   - A range / total summary ("Showing 1–20 of 137").
 *
 * Pages are 0-indexed in props (`page`, `onPageChange`) to match the page's
 * state, but displayed 1-indexed. The parent should not render this when there
 * is only one page; it is also a no-op render guard here for safety.
 */
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  ChevronFirst,
  ChevronLast,
  ChevronLeft,
  ChevronRight,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';

export interface WorkflowPaginationProps {
  /** Current page, 0-indexed. */
  page: number;
  /** Total number of pages (>= 1). */
  pageCount: number;
  /** Total number of items across all pages. */
  totalItems: number;
  /** Items per page (used to compute the displayed range). */
  pageSize: number;
  /** Called with a 0-indexed target page. */
  onPageChange: (page: number) => void;
}

/**
 * Compute the window of page numbers (1-indexed) to render, inserting the
 * sentinel `'ellipsis'` where a gap is collapsed. Always shows the first and
 * last page plus a neighborhood around the current page.
 */
function pageWindow(
  current: number,
  total: number,
): Array<number | 'ellipsis'> {
  // Small page counts render fully — no ellipsis needed.
  if (total <= 7) {
    return Array.from({ length: total }, (_, i) => i + 1);
  }
  const out: Array<number | 'ellipsis'> = [1];
  const start = Math.max(2, current - 1);
  const end = Math.min(total - 1, current + 1);
  if (start > 2) out.push('ellipsis');
  for (let p = start; p <= end; p++) out.push(p);
  if (end < total - 1) out.push('ellipsis');
  out.push(total);
  return out;
}

export function WorkflowPagination({
  page,
  pageCount,
  totalItems,
  pageSize,
  onPageChange,
}: WorkflowPaginationProps) {
  const { t } = useTranslation();
  const [jump, setJump] = useState('');

  if (pageCount <= 1) return null;

  const current1 = page + 1; // 1-indexed for display
  const rangeStart = totalItems === 0 ? 0 : page * pageSize + 1;
  const rangeEnd = Math.min(totalItems, (page + 1) * pageSize);

  const go = (target1: number) => {
    const clamped = Math.min(pageCount, Math.max(1, target1));
    onPageChange(clamped - 1);
  };

  const submitJump = () => {
    const n = Number.parseInt(jump, 10);
    if (!Number.isNaN(n)) go(n);
    setJump('');
  };

  const windowed = pageWindow(current1, pageCount);

  return (
    <div
      className="flex flex-wrap items-center justify-between gap-3"
      data-testid="wf-pagination"
    >
      <span
        className="text-sm text-muted-foreground"
        data-testid="wf-page-range"
      >
        {t('page_range', 'Showing {{start}}–{{end}} of {{total}}', {
          start: rangeStart,
          end: rangeEnd,
          total: totalItems,
        })}
      </span>

      <div className="flex flex-wrap items-center gap-2">
        <Button
          variant="outline"
          size="icon"
          className="size-8"
          data-testid="wf-page-first"
          aria-label={t('first_page', 'First page')}
          title={t('first_page', 'First page')}
          disabled={page <= 0}
          onClick={() => go(1)}
        >
          <ChevronFirst className="size-4" />
        </Button>
        <Button
          variant="outline"
          size="icon"
          className="size-8"
          data-testid="wf-page-prev"
          aria-label={t('prev_page', 'Previous')}
          title={t('prev_page', 'Previous')}
          disabled={page <= 0}
          onClick={() => go(current1 - 1)}
        >
          <ChevronLeft className="size-4" />
        </Button>

        <div className="flex items-center gap-1">
          {windowed.map((p, i) =>
            p === 'ellipsis' ? (
              <span
                key={`ellipsis-${i}`}
                className="px-1.5 text-sm text-muted-foreground select-none"
                aria-hidden="true"
                data-testid="wf-page-ellipsis"
              >
                …
              </span>
            ) : (
              <Button
                key={p}
                variant={p === current1 ? 'default' : 'outline'}
                size="sm"
                className="min-w-9"
                data-testid="wf-page-number"
                aria-current={p === current1 ? 'page' : undefined}
                onClick={() => go(p)}
              >
                {p}
              </Button>
            ),
          )}
        </div>

        <Button
          variant="outline"
          size="icon"
          className="size-8"
          data-testid="wf-page-next"
          aria-label={t('next_page', 'Next')}
          title={t('next_page', 'Next')}
          disabled={page >= pageCount - 1}
          onClick={() => go(current1 + 1)}
        >
          <ChevronRight className="size-4" />
        </Button>
        <Button
          variant="outline"
          size="icon"
          className="size-8"
          data-testid="wf-page-last"
          aria-label={t('last_page', 'Last page')}
          title={t('last_page', 'Last page')}
          disabled={page >= pageCount - 1}
          onClick={() => go(pageCount)}
        >
          <ChevronLast className="size-4" />
        </Button>

        <div className="flex items-center gap-1.5">
          <span className="text-sm text-muted-foreground whitespace-nowrap">
            {t('jump_to_page', 'Go to')}
          </span>
          <Input
            data-testid="wf-page-jump"
            className="h-8 w-16"
            inputMode="numeric"
            aria-label={t('jump_to_page', 'Go to')}
            value={jump}
            onChange={(e) => setJump(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault();
                submitJump();
              }
            }}
          />
        </div>
      </div>
    </div>
  );
}
