export type LogTimeRange = '1h' | '24h' | '7d' | '30d' | 'all' | 'custom';
export type LogSortOrder = 'desc' | 'asc';

export interface LogRangeValue {
  range: LogTimeRange;
  from: string;
  to: string;
}

export function resolveLogRange(value: LogRangeValue): { from?: string; to?: string } {
  if (value.range === 'all') return {};
  if (value.range === 'custom') {
    const from = value.from ? new Date(value.from) : null;
    const to = value.to ? new Date(value.to) : null;
    return {
      from: from && !Number.isNaN(from.getTime()) ? from.toISOString() : undefined,
      to: to && !Number.isNaN(to.getTime()) ? to.toISOString() : undefined,
    };
  }
  const duration = {
    '1h': 60 * 60 * 1000,
    '24h': 24 * 60 * 60 * 1000,
    '7d': 7 * 24 * 60 * 60 * 1000,
    '30d': 30 * 24 * 60 * 60 * 1000,
  }[value.range];
  return { from: new Date(Date.now() - duration).toISOString() };
}
