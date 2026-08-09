/**
 * i18next + react-i18next bootstrap.
 *
 * Imported once for its side effect from `main.tsx` BEFORE the React tree
 * mounts, so any `useTranslation()` call in route components finds a
 * fully-initialised instance.
 *
 * Locale persistence
 * ------------------
 * We persist the active locale under `localStorage[STORAGE_KEY]`. The
 * legacy vibecanvas (Svelte) used a different key — `LEGACY_KEY` — so on
 * first run we perform a one-shot migration: if the new key is empty and
 * the legacy key is present, copy the value over and delete the legacy
 * entry. After this runs once the legacy key is gone for good.
 *
 * Default language
 * ----------------
 * New installations default to `'en'` so the open-source distribution is
 * English-first. Existing users keep an explicitly stored `zh` preference.
 * Fallback is also `'en'`, so a missing translation renders stable public
 * copy rather than an untranslated key.
 *
 * Resource shape
 * --------------
 * We mount the ported flat string maps under the default namespace
 * (`translation`). i18next v26 enables literal-type narrowing on keys when
 * you augment the `Resources` interface — we intentionally do NOT augment
 * here so `t('any.key.string')` stays permissive across the incremental
 * migration. Tightening the key types is a follow-up once the codebase has
 * settled on a stable key surface.
 *
 * Why no `i18next-browser-languagedetector`
 * -----------------------------------------
 * The legacy product never honoured browser locale — it always defaulted
 * to zh and let the user explicitly toggle via the header switch. We keep
 * that behaviour to avoid surprising existing users mid-migration. The
 * `SettingsPage` route surfaces the toggle.
 */
import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';
import zh from './locales/zh.json';
import en from './locales/en.json';

export const STORAGE_KEY = 'vibecanvas.locale';
const LEGACY_KEY = 'vw_locale';

export type Locale = 'zh' | 'en';

function syncDocumentLocale(locale: Locale): void {
  if (typeof document === 'undefined') return;
  document.documentElement.lang = locale === 'zh' ? 'zh-CN' : 'en';
}

function isLocale(v: string | null): v is Locale {
  return v === 'zh' || v === 'en';
}

/**
 * Resolve the active locale at startup.
 *
 * Order:
 *   1. New storage key (`vibecanvas.locale`).
 *   2. Legacy storage key (`vw_locale`) — copied over to the new key and
 *      removed.
 *   3. Default `'en'`.
 */
function bootstrapLocale(): Locale {
  // `localStorage` is always defined in the browser; SSR guard is N/A
  // because this app is a pure SPA (Vite + RouterProvider).
  const stored = localStorage.getItem(STORAGE_KEY);
  if (isLocale(stored)) return stored;

  const legacy = localStorage.getItem(LEGACY_KEY);
  if (isLocale(legacy)) {
    localStorage.setItem(STORAGE_KEY, legacy);
    localStorage.removeItem(LEGACY_KEY);
    return legacy;
  }

  return 'en';
}

const initialLocale = bootstrapLocale();
syncDocumentLocale(initialLocale);

void i18n.use(initReactI18next).init({
  resources: {
    zh: { translation: zh },
    en: { translation: en },
  },
  lng: initialLocale,
  fallbackLng: 'en',
  interpolation: {
    // React already escapes interpolated values, so i18next shouldn't
    // double-escape — this is the standard react-i18next recipe.
    escapeValue: false,
  },
});

/**
 * Change the active language and persist it under the canonical storage key.
 *
 * Components SHOULD call this rather than `i18n.changeLanguage` directly so
 * the localStorage write stays co-located with the runtime switch.
 */
export function setLocale(lng: Locale): void {
  syncDocumentLocale(lng);
  void i18n.changeLanguage(lng);
  localStorage.setItem(STORAGE_KEY, lng);
}

// Keep the document contract correct even if a test, extension bridge, or a
// future settings surface calls i18next directly instead of setLocale.
i18n.on('languageChanged', (language) => {
  syncDocumentLocale(language.startsWith('zh') ? 'zh' : 'en');
});

export default i18n;
