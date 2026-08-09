import { useEffect, useState } from 'react';
import { ShieldAlert } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import { useAuthStore } from '@/stores/auth';

const ACTIVE_POLL_MS = 5_000;
const INACTIVE_POLL_MS = 30_000;

export function PrivilegedSupportBanner() {
  const { t } = useTranslation();
  const privileged = useAuthStore((state) => state.privilegedAccess);
  const exitPrivilegedAccess = useAuthStore((state) => state.exitPrivilegedAccess);
  const refreshPrivilegedAccess = useAuthStore((state) => state.refreshPrivilegedAccess);
  const [exiting, setExiting] = useState(false);
  const [exitFailed, setExitFailed] = useState(false);

  const active = privileged !== null;
  const expiresAt = privileged?.expiresAt ?? '';

  useEffect(() => {
    let cancelled = false;
    let timer = 0;
    const schedule = () => {
      const expiryDelay = expiresAt
        ? Math.max(250, Date.parse(expiresAt) - Date.now() + 100)
        : ACTIVE_POLL_MS;
      const delay = active
        ? Math.min(ACTIVE_POLL_MS, expiryDelay)
        : INACTIVE_POLL_MS;
      timer = window.setTimeout(async () => {
        await refreshPrivilegedAccess();
        if (!cancelled) schedule();
      }, delay);
    };
    schedule();
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [active, expiresAt, refreshPrivilegedAccess]);

  if (!privileged) return null;

  const scope = privileged.resourceType && privileged.resourceId
    ? `${privileged.resourceType}:${privileged.resourceId}`
    : t('privilegedSupport.organizationScope', 'organization metadata');
  const expiresLabel = new Intl.DateTimeFormat(undefined, {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  }).format(new Date(privileged.expiresAt));

  const handleExit = async () => {
    setExiting(true);
    setExitFailed(false);
    try {
      await exitPrivilegedAccess();
    } catch {
      setExitFailed(true);
      setExiting(false);
    }
  };

  return (
    <div
      role="alert"
      aria-live="assertive"
      data-testid="privileged-support-banner"
      className="relative z-header flex shrink-0 flex-wrap items-center gap-x-4 gap-y-2 border-b border-amber-500/40 bg-amber-100 px-3 py-2 text-amber-950 shadow-sm dark:bg-amber-950 dark:text-amber-50"
    >
      <div className="flex min-w-0 flex-1 items-center gap-2">
        <ShieldAlert className="size-4 shrink-0" aria-hidden="true" />
        <p className="min-w-0 text-sm">
          <span className="font-semibold">
            {t('privilegedSupport.title', 'Privileged support mode')}
          </span>
          {' · '}
          {t('privilegedSupport.scopeLabel', 'Scope')}: {scope}
          {' · '}
          {t('privilegedSupport.actionsLabel', 'Actions')}:{' '}
          {privileged.actions.join(', ')}
          {' · '}
          {t('privilegedSupport.expiresLabel', 'Expires at')}: {expiresLabel}
        </p>
      </div>
      {exitFailed ? (
        <span className="text-xs font-medium" role="status">
          {t('privilegedSupport.exitFailed', 'Could not exit. Try again.')}
        </span>
      ) : null}
      <Button
        type="button"
        variant="outline"
        size="sm"
        aria-busy={exiting}
        disabled={exiting}
        className="border-amber-700/50 bg-transparent text-current hover:bg-amber-200/60 hover:text-current dark:hover:bg-amber-900"
        onClick={() => { void handleExit(); }}
      >
        {exiting
          ? t('privilegedSupport.exiting', 'Exiting…')
          : t('privilegedSupport.exit', 'Exit support mode')}
      </Button>
    </div>
  );
}
