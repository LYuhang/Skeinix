import i18n from '@/lib/i18n';

function currentLocale(): string {
  const language = i18n.resolvedLanguage ?? i18n.language;
  return language.startsWith('zh') ? 'zh-CN' : 'en';
}

export function formatNumber(
  value: number,
  options?: Intl.NumberFormatOptions,
): string {
  return new Intl.NumberFormat(currentLocale(), options).format(value);
}
