/**
 * Render-time error boundary.
 *
 * Catches errors thrown during render / lifecycle of descendant components,
 * logs them to the UI store's bounded error log, and renders a recovery UI
 * with "Reload page" and "Copy error details" actions.
 *
 * Event-handler and async errors are NOT caught here — those flow through
 * window.onerror / promise rejection handlers (added in a later task).
 *
 * The `scope` prop tags log entries so the global vs. per-page boundaries
 * are distinguishable in the error log.
 */
import { Component, type ErrorInfo, type ReactNode } from 'react';
import { AlertTriangle, House, RotateCcw } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import { Button } from '@/components/ui/button';
import { CopyButton } from '@/components/ui/copy-button';
import { getBasePath } from '@/lib/base-path';
import { useUIStore } from '@/stores/ui';

export interface ErrorBoundaryProps {
  children: ReactNode;
  scope?: string;
}

interface ErrorBoundaryState {
  error: Error | null;
  errorInfo: ErrorInfo | null;
}

function GlobalErrorFallback({
  message,
  details,
  onReload,
}: {
  message: string;
  details: string;
  onReload: () => void;
}) {
  const { t } = useTranslation();
  return (
    <main className="flex min-h-screen items-center justify-center bg-surface-app p-8">
      <section
        role="alert"
        className="w-full max-w-xl rounded-xl border border-edge-structural bg-surface-raised p-6 shadow-modal"
      >
        <div className="mb-4 flex h-10 w-10 items-center justify-center rounded-lg bg-state-danger/10 text-state-danger">
          <AlertTriangle className="h-5 w-5" aria-hidden="true" />
        </div>
        <h1 className="text-xl font-semibold text-foreground">
          {t('errorBoundary.title', 'Something went wrong')}
        </h1>
        <p className="mt-2 text-sm leading-6 text-muted-foreground">
          {t(
            'errorBoundary.description',
            'The current view could not be rendered. Reload it, return to the workspace, or copy the details for support.',
          )}
        </p>
        <details className="mt-4 rounded-lg border border-edge-subtle bg-surface-sunken">
          <summary className="cursor-pointer px-3 py-2 text-sm font-medium">
            {t('errorBoundary.details', 'Error details')}
          </summary>
          <pre className="max-h-40 overflow-auto border-t border-edge-subtle p-3 text-xs text-muted-foreground">
            {message}
          </pre>
        </details>
        <div className="mt-5 flex flex-wrap gap-2">
          <Button type="button" onClick={onReload}>
            <RotateCcw className="h-4 w-4" aria-hidden="true" />
            {t('errorBoundary.reload', 'Reload view')}
          </Button>
          <Button
            type="button"
            variant="secondary"
            onClick={() => window.location.assign(`${getBasePath()}/workspace`)}
          >
            <House className="h-4 w-4" aria-hidden="true" />
            {t('errorBoundary.workspace', 'Back to workspace')}
          </Button>
          <CopyButton
            value={details}
            showLabel
            variant="outline"
            label={t('errorBoundary.copy', 'Copy details')}
            copiedLabel={t('errorBoundary.copied', 'Details copied')}
          />
        </div>
      </section>
    </main>
  );
}

export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { error: null, errorInfo: null };

  static getDerivedStateFromError(error: Error): Partial<ErrorBoundaryState> {
    return { error };
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo): void {
    const scope = this.props.scope ?? 'unknown';
    this.setState({ errorInfo });
    // Use getState() — class components can read Zustand outside the React tree.
    useUIStore.getState().logError(`[${scope}] ${error.message}`);
    // Keep the original stack visible in the devtools console.
    console.error(`[ErrorBoundary:${scope}]`, error, errorInfo);
  }

  handleReload = (): void => {
    window.location.reload();
  };

  render(): ReactNode {
    const { error, errorInfo } = this.state;
    if (!error) return this.props.children;
    const details = [
      `Scope: ${this.props.scope ?? 'unknown'}`,
      `Message: ${error.message}`,
      `Stack: ${error.stack ?? '(no stack)'}`,
      `Component stack: ${errorInfo?.componentStack ?? '(no component stack)'}`,
    ].join('\n\n');
    return <GlobalErrorFallback message={error.message} details={details} onReload={this.handleReload} />;
  }
}
