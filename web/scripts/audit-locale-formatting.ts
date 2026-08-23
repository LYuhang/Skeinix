import assert from 'node:assert/strict';

const values = new Map<string, string>();
Object.defineProperty(globalThis, 'localStorage', {
  configurable: true,
  value: {
    getItem(key: string) {
      return values.get(key) ?? null;
    },
    removeItem(key: string) {
      values.delete(key);
    },
    setItem(key: string, value: string) {
      values.set(key, value);
    },
  },
});

const [{ default: i18n }, { formatNumber }, { formatDateTime }, { describeCronExpression }] = await Promise.all([
  import('../src/lib/i18n/index.ts'),
  import('../src/lib/format/number.ts'),
  import('../src/lib/timezone.ts'),
  import('../src/lib/cron-description.ts'),
]);

const instant = '2025-12-31T23:45:00Z';
for (const locale of ['en', 'zh'] as const) {
  await i18n.changeLanguage(locale);
  const intlLocale = locale === 'zh' ? 'zh-CN' : 'en';
  assert.equal(
    formatDateTime(instant, { timeZone: 'UTC', dateStyle: 'medium', timeStyle: 'short' }),
    new Intl.DateTimeFormat(intlLocale, {
      timeZone: 'UTC',
      dateStyle: 'medium',
      timeStyle: 'short',
    }).format(new Date(instant)),
  );
  assert.equal(
    formatNumber(1234.5, { style: 'currency', currency: 'CNY' }),
    new Intl.NumberFormat(intlLocale, { style: 'currency', currency: 'CNY' }).format(1234.5),
  );
  assert.deepEqual(
    describeCronExpression('15 8 * * 1-5', locale),
    locale === 'zh'
      ? { text: '每周一至周五 08:15', valid: true }
      : { text: '08:15 AM, every Monday through Friday', valid: true },
  );
}

process.stdout.write('Locale formatting audit passed: dates, numbers, and schedules cover en and zh.\n');
