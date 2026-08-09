/**
 * "View full" affordance shared by the text/terminal renderers.
 *
 * Renders a button that lazy-loads the full VFS body ON CLICK (via
 * `useVfsFull`), then shows it in a max-height scroll region. When the
 * backend truncated the response at the 256 KB cap it shows a subtle banner.
 *
 * The body is shown in a mono pre by default (`render` prop can override for
 * e.g. markdown). Disabled (button hidden) when there is no `path`.
 */
import { Loader2 } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useVfsFull } from './useVfsFull';

export interface ViewFullPanelProps {
  wfId: string | undefined;
  path: string | undefined;
  /** Optional custom renderer for the fetched body (default: mono pre). */
  render?: (content: string) => React.ReactNode;
}

export function ViewFullPanel({ wfId, path, render }: ViewFullPanelProps) {
  const { t } = useTranslation();
  const { content, truncated, loading, error, load } = useVfsFull(wfId, path);

  if (!path) return null;

  return (
    <div className="mt-1">
      {content === null ? (
        <button
          type="button"
          onClick={load}
          disabled={loading}
          data-action="tool-view-full"
          className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium text-primary hover:underline focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:opacity-60"
        >
          {loading && <Loader2 className="h-3 w-3 animate-spin motion-reduce:animate-none" />}
          {t('tool.view_full')}
        </button>
      ) : (
        <div className="space-y-1">
          {truncated && (
            <div
              className="rounded bg-muted/60 px-2 py-1 text-xs text-muted-foreground"
              data-role="vfs-truncated-banner"
            >
              {t('tool.truncated_banner')}
            </div>
          )}
          {render ? (
            render(content)
          ) : (
            <pre className="max-h-96 overflow-auto whitespace-pre-wrap break-words rounded border bg-muted/40 p-2 font-mono text-xs leading-snug">
              {content}
            </pre>
          )}
        </div>
      )}
      {error && (
        <div className="mt-1 text-xs text-destructive" data-role="vfs-error">
          {error}
        </div>
      )}
    </div>
  );
}
