/**
 * Human-readable byte sizes for the Explorer / file views.
 *
 * Below 1 KB shows raw bytes ("742 B"); above, scales by 1024 through
 * KB/MB/GB/TB/PB with up to one decimal, trailing ".0" trimmed ("1.5 MB",
 * "2 KB"). Used everywhere a VFS entry's `size_bytes` is shown.
 */
const UNITS = ['KB', 'MB', 'GB', 'TB', 'PB'] as const;

export function formatBytes(bytes: number, decimals = 1): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 B';
  if (bytes < 1024) return `${Math.round(bytes)} B`;
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < UNITS.length - 1) {
    value /= 1024;
    unit += 1;
  }
  const text = value.toFixed(decimals).replace(/\.0+$/, '');
  return `${text} ${UNITS[unit]}`;
}
