import { useEffect, useRef, type RefObject } from 'react';
import { useTranslation } from 'react-i18next';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import type { LogRangeValue, LogSortOrder, LogTimeRange } from '@/lib/log-history';

export function LogHistoryControls({
  value,
  order,
  onValueChange,
  onOrderChange,
  children,
}: {
  value: LogRangeValue;
  order: LogSortOrder;
  onValueChange: (value: LogRangeValue) => void;
  onOrderChange: (order: LogSortOrder) => void;
  children?: React.ReactNode;
}) {
  const { t } = useTranslation();
  return (
    <div className="flex flex-wrap items-end gap-3 rounded-lg border border-edge-subtle bg-surface-sunken/35 p-3">
      <div className="min-w-40 flex-1 space-y-1.5 sm:max-w-52">
        <Label>{t('logs.timeRange', 'Time range')}</Label>
        <Select
          value={value.range}
          onValueChange={(range) => onValueChange({ ...value, range: range as LogTimeRange })}
        >
          <SelectTrigger aria-label={t('logs.timeRange', 'Time range')}><SelectValue /></SelectTrigger>
          <SelectContent>
            {(['1h', '24h', '7d', '30d', 'all', 'custom'] as const).map((range) => (
              <SelectItem key={range} value={range}>{t(`logs.range.${range}`, range)}</SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      {value.range === 'custom' ? (
        <>
          <div className="min-w-52 flex-1 space-y-1.5">
            <Label htmlFor="log-range-from">{t('logs.from', 'From')}</Label>
            <Input
              id="log-range-from"
              type="datetime-local"
              value={value.from}
              onChange={(event) => onValueChange({ ...value, from: event.target.value })}
            />
          </div>
          <div className="min-w-52 flex-1 space-y-1.5">
            <Label htmlFor="log-range-to">{t('logs.to', 'To')}</Label>
            <Input
              id="log-range-to"
              type="datetime-local"
              value={value.to}
              onChange={(event) => onValueChange({ ...value, to: event.target.value })}
            />
          </div>
        </>
      ) : null}
      <div className="min-w-40 flex-1 space-y-1.5 sm:max-w-52">
        <Label>{t('logs.sort', 'Sort')}</Label>
        <Select value={order} onValueChange={(next) => onOrderChange(next as LogSortOrder)}>
          <SelectTrigger aria-label={t('logs.sort', 'Sort')}><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="desc">{t('logs.sort.desc', 'Newest first')}</SelectItem>
            <SelectItem value="asc">{t('logs.sort.asc', 'Oldest first')}</SelectItem>
          </SelectContent>
        </Select>
      </div>
      {children}
    </div>
  );
}

export function IncrementalLogLoader({
  hasMore,
  loading,
  onLoadMore,
  order,
  rootRef,
}: {
  hasMore: boolean;
  loading: boolean;
  onLoadMore: () => void;
  order: LogSortOrder;
  rootRef?: RefObject<Element | null>;
}) {
  const { t } = useTranslation();
  const sentinelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const target = sentinelRef.current;
    if (!target || !hasMore || loading || typeof IntersectionObserver === 'undefined') return;
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) onLoadMore();
    }, {
      root: rootRef?.current ?? null,
      rootMargin: '160px',
    });
    observer.observe(target);
    return () => observer.disconnect();
  }, [hasMore, loading, onLoadMore, rootRef]);

  if (!hasMore) return null;
  return (
    <div ref={sentinelRef} className="flex justify-center py-4">
      <Button variant="outline" size="sm" onClick={onLoadMore} disabled={loading}>
        {loading
          ? t('logs.loadingMore', 'Loading…')
          : order === 'desc'
            ? t('logs.loadOlder', 'Load older records')
            : t('logs.loadNewer', 'Load newer records')}
      </Button>
    </div>
  );
}
