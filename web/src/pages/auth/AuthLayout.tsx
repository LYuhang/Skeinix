import { type LucideIcon } from 'lucide-react';
import { type ReactNode, useEffect } from 'react';
import {
  Bot,
  Languages,
  ListChecks,
  Rocket,
  Workflow,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { AppIcon } from '@/app/AppIcon';
import { setLocale, type Locale } from '@/lib/i18n';

interface Capability {
  key: string;
  fallback: string;
  icon: LucideIcon;
  colorClass: string;
  surfaceClass: string;
}

const CAPABILITIES: readonly Capability[] = [
  {
    key: 'auth_capability_agent',
    fallback: 'Agent',
    icon: Bot,
    colorClass: 'text-resource-chat',
    surfaceClass: 'bg-resource-chat/10 ring-resource-chat/20',
  },
  {
    key: 'auth_capability_workflow',
    fallback: 'Workflow',
    icon: Workflow,
    colorClass: 'text-resource-workflow',
    surfaceClass: 'bg-resource-workflow/10 ring-resource-workflow/20',
  },
  {
    key: 'auth_capability_task',
    fallback: 'Task',
    icon: ListChecks,
    colorClass: 'text-resource-task',
    surfaceClass: 'bg-resource-task/10 ring-resource-task/20',
  },
  {
    key: 'auth_capability_deployment',
    fallback: 'Deployment',
    icon: Rocket,
    colorClass: 'text-resource-deployment',
    surfaceClass: 'bg-resource-deployment/10 ring-resource-deployment/20',
  },
];

const LANGUAGES: ReadonlyArray<{ value: Locale; label: string }> = [
  { value: 'zh', label: '中文' },
  { value: 'en', label: 'EN' },
];

export interface AuthLayoutProps {
  title: string;
  subtitle?: string;
  children: ReactNode;
}

export function AuthLayout({ title, subtitle, children }: AuthLayoutProps) {
  const { t, i18n } = useTranslation();
  const activeLocale: Locale = (i18n.resolvedLanguage ?? i18n.language)
    .startsWith('zh') ? 'zh' : 'en';
  useEffect(() => {
    document.title = `${title} · Skeinix`;
  }, [title]);

  return (
    <main className="relative flex min-h-screen w-full items-center justify-center bg-surface-app px-5 pb-8 pt-20 text-foreground sm:px-8 lg:px-12 lg:py-8">
      <div
        className="absolute right-5 top-5 flex h-9 items-center gap-1 rounded-lg border border-edge-structural bg-surface-raised p-1 sm:right-8 sm:top-6"
        role="group"
        aria-label={t('settings_language', 'Language')}
      >
        <Languages className="mx-1 size-4 text-muted-foreground" aria-hidden="true" />
        {LANGUAGES.map((language) => {
          const selected = activeLocale === language.value;
          return (
            <button
              key={language.value}
              type="button"
              className={`h-7 rounded-md px-2.5 text-xs font-medium transition-colors duration-feedback focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 focus-visible:ring-offset-surface-raised ${
                selected
                  ? 'bg-primary text-primary-foreground'
                  : 'text-muted-foreground hover:bg-surface-hover hover:text-foreground'
              }`}
              aria-pressed={selected}
              onClick={() => setLocale(language.value)}
            >
              {language.label}
            </button>
          );
        })}
      </div>

      <div className="grid w-full max-w-[64rem] items-center gap-14 lg:grid-cols-[1fr_25rem] lg:gap-20">
        <aside className="hidden lg:block">
          <div className="flex items-center gap-3.5">
            <AppIcon
              alt=""
              aria-hidden="true"
              className="size-14 rounded-[15px]"
            />
            <span className="text-[1.75rem] font-semibold leading-8 tracking-[-0.025em]">
              {t('ws_title', 'Skeinix')}
            </span>
          </div>

          <div className="mt-12 max-w-[34rem]">
            <h2 className="text-balance text-[2rem] font-semibold leading-[1.16] tracking-[-0.03em] text-foreground xl:text-[2.25rem]">
              {t(
                'auth_hero_title',
                'Build, preview, automate, and deploy with AI agents, visual workflows, tasks, and your browser—all in one platform.',
              )}
            </h2>
            <p className="mt-5 max-w-[31rem] text-pretty text-base leading-7 text-muted-foreground">
              {t(
                'auth_brand_tagline',
                'Conversation, visual orchestration, debugging, and execution—together in one workspace.',
              )}
            </p>

            <div
              className="relative mt-10 grid max-w-[28rem] grid-cols-4"
              role="list"
              aria-label={t('auth_capabilities_label', 'Core capabilities')}
            >
              <span
                className="absolute left-[1.375rem] right-[calc(25%-1.375rem)] top-[1.375rem] h-px bg-edge-structural"
                aria-hidden="true"
              />
              {CAPABILITIES.map((capability, index) => {
                const Icon = capability.icon;
                return (
                  <div
                    key={capability.key}
                    className="relative flex min-w-0 flex-col items-start gap-2.5 text-left"
                    role="listitem"
                  >
                    <span
                      className={`auth-capability-node grid size-11 place-items-center rounded-xl ring-1 ring-inset ${capability.colorClass} ${capability.surfaceClass}`}
                      style={{ animationDelay: `${index * 520}ms` }}
                      aria-hidden="true"
                    >
                      <Icon className="size-5" />
                    </span>
                    <span className="max-w-full truncate px-1 text-xs font-medium text-content-secondary">
                      {t(capability.key, capability.fallback)}
                    </span>
                  </div>
                );
              })}
            </div>
          </div>
        </aside>

        <section className="min-w-0">
          <div className="mx-auto w-full max-w-[25rem] rounded-2xl border border-edge-structural bg-surface-raised px-6 py-7 shadow-raised sm:px-8 sm:py-8 [&_button[type=submit]]:h-11 [&_input]:h-11">
            <div className="mb-7 flex items-center gap-3 lg:hidden">
              <AppIcon
                alt=""
                aria-hidden="true"
                className="size-12 rounded-[13px]"
              />
              <span className="text-[1.375rem] font-semibold tracking-[-0.02em]">
                {t('ws_title', 'Skeinix')}
              </span>
            </div>

            <div className="mb-6 flex flex-col gap-1.5">
              <h1 className="text-2xl font-semibold leading-8 tracking-[-0.025em]">
                {title}
              </h1>
              {subtitle ? (
                <p className="text-sm leading-6 text-muted-foreground">{subtitle}</p>
              ) : null}
            </div>

            {children}
          </div>
        </section>
      </div>
    </main>
  );
}
