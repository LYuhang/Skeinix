import { useQuery } from '@tanstack/react-query';
import { Activity, Clock3, RefreshCw } from 'lucide-react';
import { useMemo } from 'react';
import type { TFunction } from 'i18next';
import { useTranslation } from 'react-i18next';

import { ActionableError } from '@/components/presentation/ActionableError';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import {
  getCodexAccountUsage,
  type CodexAccountUsage,
  type CodexRateLimitBucket,
  type CodexRateLimitWindow,
} from '@/lib/api/agent-runtime';
import { codexAccountUsageQueryKey } from '@/lib/api/queries/agent-runtime';
import { cn } from '@/lib/utils';

const POLL_INTERVAL_MS = 60_000;
const ACTIVITY_WEEKS = 26;

function utcDateKey(date: Date): string {
  return date.toISOString().slice(0, 10);
}

function activityDays(): Array<{ date: string; future: boolean }> {
  const today = new Date();
  const todayUtc = new Date(Date.UTC(
    today.getUTCFullYear(),
    today.getUTCMonth(),
    today.getUTCDate(),
  ));
  const start = new Date(todayUtc);
  start.setUTCDate(start.getUTCDate() - start.getUTCDay() - (ACTIVITY_WEEKS - 1) * 7);
  return Array.from({ length: ACTIVITY_WEEKS * 7 }, (_, index) => {
    const date = new Date(start);
    date.setUTCDate(start.getUTCDate() + index);
    return { date: utcDateKey(date), future: date > todayUtc };
  });
}

function heatLevel(tokens: number, peak: number): number {
  if (tokens <= 0 || peak <= 0) return 0;
  return Math.max(1, Math.min(4, Math.ceil(
    (Math.log1p(tokens) / Math.log1p(peak)) * 4,
  )));
}

function formatDuration(minutes: number | null, t: TFunction): string {
  if (minutes === null) return t('settings_codex_usage_window', 'Usage window');
  if (minutes % 10_080 === 0) {
    const weeks = minutes / 10_080;
    return t('settings_codex_usage_weeks', '{{count}}-week window', { count: weeks });
  }
  if (minutes % 1_440 === 0) {
    const days = minutes / 1_440;
    return t('settings_codex_usage_days', '{{count}}-day window', { count: days });
  }
  if (minutes % 60 === 0) {
    const hours = minutes / 60;
    return t('settings_codex_usage_hours', '{{count}}-hour window', { count: hours });
  }
  return t('settings_codex_usage_minutes', '{{count}}-minute window', { count: minutes });
}

function UsageActivity({ usage }: { usage: CodexAccountUsage }) {
  const { t, i18n } = useTranslation();
  const number = useMemo(
    () => new Intl.NumberFormat(i18n.resolvedLanguage ?? i18n.language, {
      notation: 'compact',
      maximumFractionDigits: 1,
    }),
    [i18n.language, i18n.resolvedLanguage],
  );
  const tokenByDate = useMemo(
    () => new Map(usage.daily_usage_buckets.map((bucket) => [bucket.start_date, bucket.tokens])),
    [usage.daily_usage_buckets],
  );
  const days = useMemo(() => activityDays(), []);
  const peak = Math.max(0, ...usage.daily_usage_buckets.map((bucket) => bucket.tokens));
  const visibleStart = days[0]?.date ?? '';
  const visibleEnd = days.findLast((day) => !day.future)?.date ?? '';
  const unavailable = usage.unavailable_sections.includes('activity');

  return (
    <section className="pt-4" aria-labelledby="codex-token-activity-title">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h5 id="codex-token-activity-title" className="flex items-center gap-2 text-sm font-medium">
            <Activity className="size-4 text-muted-foreground" aria-hidden="true" />
            {t('settings_codex_usage_activity', 'Token activity')}
          </h5>
          <p className="mt-1 text-xs text-muted-foreground">
            {t('settings_codex_usage_activity_range', 'Daily account usage · last 26 weeks')}
          </p>
        </div>
        {usage.usage_summary ? (
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
            {usage.usage_summary.lifetime_tokens !== null ? (
              <span>
                <strong className="font-medium tabular-nums text-foreground">
                  {number.format(usage.usage_summary.lifetime_tokens)}
                </strong>{' '}
                {t('settings_codex_usage_lifetime', 'lifetime tokens')}
              </span>
            ) : null}
            {usage.usage_summary.current_streak_days !== null ? (
              <span>
                <strong className="font-medium tabular-nums text-foreground">
                  {usage.usage_summary.current_streak_days}
                </strong>{' '}
                {t('settings_codex_usage_streak', 'day streak')}
              </span>
            ) : null}
          </div>
        ) : null}
      </div>

      {unavailable ? (
        <p className="mt-3 text-sm text-muted-foreground">
          {t('settings_codex_usage_activity_unavailable', 'Activity history is not available for this account.')}
        </p>
      ) : (
        <>
          <div
            className="mt-3 max-w-full overflow-x-auto pb-1"
            role="img"
            aria-label={t(
              'settings_codex_usage_activity_label',
              'Codex token activity from {{start}} to {{end}}',
              { start: visibleStart, end: visibleEnd },
            )}
          >
            <div
              className="grid w-max grid-flow-col grid-rows-7 gap-[3px]"
              style={{ gridAutoColumns: '10px' }}
              aria-hidden="true"
            >
              {days.map((day) => {
                const tokens = tokenByDate.get(day.date) ?? 0;
                const level = heatLevel(tokens, peak);
                return (
                  <span
                    key={day.date}
                    title={day.future ? undefined : t(
                      'settings_codex_usage_day_tooltip',
                      '{{date}} · {{tokens}} tokens',
                      { date: day.date, tokens: number.format(tokens) },
                    )}
                    className={cn(
                      'size-2.5 rounded-[2px] ring-1 ring-inset ring-edge-subtle/70',
                      day.future && 'invisible',
                      level === 0 && 'bg-surface-subtle',
                      level === 1 && 'bg-primary/20',
                      level === 2 && 'bg-primary/40',
                      level === 3 && 'bg-primary/65',
                      level === 4 && 'bg-primary',
                    )}
                  />
                );
              })}
            </div>
          </div>
          <div className="mt-2 flex items-center justify-end gap-1.5 text-xs text-muted-foreground" aria-hidden="true">
            <span>{t('settings_codex_usage_less', 'Less')}</span>
            {[0, 1, 2, 3, 4].map((level) => (
              <span
                key={level}
                className={cn(
                  'size-2.5 rounded-[2px] ring-1 ring-inset ring-edge-subtle/70',
                  level === 0 && 'bg-surface-subtle',
                  level === 1 && 'bg-primary/20',
                  level === 2 && 'bg-primary/40',
                  level === 3 && 'bg-primary/65',
                  level === 4 && 'bg-primary',
                )}
              />
            ))}
            <span>{t('settings_codex_usage_more', 'More')}</span>
          </div>
        </>
      )}
    </section>
  );
}

interface WindowView {
  id: string;
  label: string;
  window: CodexRateLimitWindow;
  bucket: CodexRateLimitBucket;
}

function RateLimitProgress({ item }: { item: WindowView }) {
  const { t, i18n } = useTranslation();
  const used = Math.min(100, Math.max(0, item.window.used_percent));
  const remaining = Math.max(0, 100 - used);
  const reset = item.window.resets_at === null
    ? null
    : new Intl.DateTimeFormat(i18n.resolvedLanguage ?? i18n.language, {
      dateStyle: 'medium',
      timeStyle: 'short',
    }).format(new Date(item.window.resets_at * 1000));

  return (
    <div className="py-3 first:pt-0 last:pb-0">
      <div className="flex items-baseline justify-between gap-3">
        <span className="min-w-0 truncate text-sm font-medium" title={item.label}>
          {item.label}
        </span>
        <span className="shrink-0 text-xs tabular-nums text-muted-foreground">
          {t('settings_codex_usage_remaining', '{{remaining}}% remaining', {
            remaining: Math.round(remaining),
          })}
        </span>
      </div>
      <div
        className="mt-2 h-2 overflow-hidden rounded-full bg-content-tertiary/15"
        role="progressbar"
        aria-label={item.label}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(used)}
        aria-valuetext={t('settings_codex_usage_used', '{{used}}% used', { used: Math.round(used) })}
      >
        <div
          className={cn(
            'h-full rounded-full transition-[width] duration-feedback motion-reduce:transition-none',
            used >= 90 ? 'bg-destructive' : used >= 75 ? 'bg-state-warning' : 'bg-primary',
          )}
          style={{ width: `${used}%` }}
        />
      </div>
      <div className="mt-1.5 flex flex-wrap items-center justify-between gap-x-3 gap-y-1 text-xs text-muted-foreground">
        <span>{t('settings_codex_usage_used', '{{used}}% used', { used: Math.round(used) })}</span>
        {reset ? (
          <span className="inline-flex items-center gap-1">
            <Clock3 className="size-3" aria-hidden="true" />
            {t('settings_codex_usage_resets', 'Resets {{time}}', { time: reset })}
          </span>
        ) : null}
      </div>
      {item.bucket.rate_limit_reached_type ? (
        <p className="mt-2 text-xs text-destructive">
          {t('settings_codex_usage_limit_reached', 'This usage limit has been reached.')}
        </p>
      ) : null}
    </div>
  );
}

function UsageLimits({ usage }: { usage: CodexAccountUsage }) {
  const { t } = useTranslation();
  const windows = useMemo<WindowView[]>(() => usage.rate_limits.flatMap((bucket) => {
    const bucketLabel = bucket.limit_name && bucket.limit_name !== bucket.limit_id
      ? bucket.limit_name
      : null;
    return ([['primary', bucket.primary], ['secondary', bucket.secondary]] as const)
      .filter((entry): entry is readonly ['primary' | 'secondary', CodexRateLimitWindow] => entry[1] !== null)
      .map(([kind, window]) => {
        const duration = formatDuration(window.window_duration_mins, t);
        return {
          id: `${bucket.limit_id}:${kind}`,
          label: bucketLabel ? `${bucketLabel} · ${duration}` : duration,
          window,
          bucket,
        };
      });
  }), [t, usage.rate_limits]);
  const unavailable = usage.unavailable_sections.includes('rate_limits');
  const credits = usage.rate_limits.find((bucket) => bucket.credits)?.credits ?? null;

  return (
    <section className="mt-4 border-t border-edge-subtle pt-4" aria-labelledby="codex-rate-limits-title">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h5 id="codex-rate-limits-title" className="text-sm font-medium">
            {t('settings_codex_usage_limits', 'Usage limits')}
          </h5>
          <p className="mt-1 text-xs text-muted-foreground">
            {t('settings_codex_usage_limits_help', 'Live account windows reported by Codex.')}
          </p>
        </div>
        {credits ? (
          <span className="rounded-full bg-surface-subtle px-2.5 py-1 text-xs text-muted-foreground">
            {credits.unlimited
              ? t('settings_codex_usage_unlimited_credits', 'Unlimited credits')
              : credits.balance
                ? t('settings_codex_usage_credit_balance', '{{balance}} credits', { balance: credits.balance })
                : credits.has_credits
                  ? t('settings_codex_usage_credits_available', 'Credits available')
                  : t('settings_codex_usage_no_credits', 'No extra credits')}
          </span>
        ) : null}
      </div>
      {unavailable ? (
        <p className="mt-3 text-sm text-muted-foreground">
          {t('settings_codex_usage_limits_unavailable', 'Rate-limit status is not available for this account.')}
        </p>
      ) : windows.length === 0 ? (
        <p className="mt-3 text-sm text-muted-foreground">
          {t('settings_codex_usage_no_limits', 'Codex did not report an active usage window.')}
        </p>
      ) : (
        <div className="mt-3 divide-y divide-edge-subtle">
          {windows.map((item) => <RateLimitProgress key={item.id} item={item} />)}
        </div>
      )}
    </section>
  );
}

export function CodexAccountUsagePanel() {
  const { t, i18n } = useTranslation();
  const usageQuery = useQuery({
    queryKey: codexAccountUsageQueryKey,
    queryFn: getCodexAccountUsage,
    staleTime: 20_000,
    retry: 1,
    refetchOnWindowFocus: true,
    refetchIntervalInBackground: false,
    refetchInterval: () => (
      typeof document !== 'undefined' && document.visibilityState === 'visible'
        ? POLL_INTERVAL_MS
        : false
    ),
  });
  const usage = usageQuery.data;
  const fetched = usage
    ? new Intl.DateTimeFormat(i18n.resolvedLanguage ?? i18n.language, {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    }).format(new Date(usage.fetched_at))
    : null;

  return (
    <div className="mt-4 border-t border-edge-subtle pt-4" data-testid="codex-account-usage">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h4 className="text-sm font-medium">
            {t('settings_codex_usage_title', 'Usage and availability')}
          </h4>
          {usage ? (
            <p className="mt-1 truncate text-xs text-muted-foreground">
              {[usage.email, usage.plan_type ? usage.plan_type.toUpperCase() : null]
                .filter(Boolean)
                .join(' · ')}
            </p>
          ) : (
            <p className="mt-1 text-xs text-muted-foreground">
              {t('settings_codex_usage_polling', 'Updates automatically while this page is open.')}
            </p>
          )}
        </div>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="h-8 shrink-0 gap-1.5"
          onClick={() => void usageQuery.refetch()}
          disabled={usageQuery.isFetching}
          aria-label={t('settings_codex_usage_refresh', 'Refresh usage')}
        >
          <RefreshCw className={cn('size-3.5', usageQuery.isFetching && 'animate-spin motion-reduce:animate-none')} aria-hidden="true" />
          <span>{t('settings_codex_usage_refresh_short', 'Refresh')}</span>
        </Button>
      </div>

      {usageQuery.isPending ? (
        <div className="mt-4 space-y-3" aria-label={t('settings_codex_usage_loading', 'Loading account usage')}>
          <Skeleton className="h-16 w-full" />
          <Skeleton className="h-20 w-full" />
        </div>
      ) : usageQuery.isError ? (
        <ActionableError
          className="mt-4"
          title={t('settings_codex_usage_error', 'Usage could not be loaded')}
          description={usageQuery.error instanceof Error ? usageQuery.error.message : String(usageQuery.error)}
          actionLabel={t('settings_codex_usage_retry', 'Try again')}
          onAction={() => void usageQuery.refetch()}
        />
      ) : usage ? (
        <>
          <UsageActivity usage={usage} />
          <UsageLimits usage={usage} />
          {fetched ? (
            <p className="mt-4 border-t border-edge-subtle pt-3 text-xs text-muted-foreground" aria-live="polite">
              {t('settings_codex_usage_updated', 'Last updated {{time}} · refreshes every minute', { time: fetched })}
            </p>
          ) : null}
        </>
      ) : null}
    </div>
  );
}
