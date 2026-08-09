/**
 * Sandboxed HTML renderer for `text/html`.
 *
 * CSP DECISION (verified 2026-06-19): the app ships NO Content-Security-Policy
 * — `web/index.html` has no CSP `<meta>`, the Vite dev/build/preview config
 * sets no `Content-Security-Policy` / `frame-src` header, and a repo-wide grep
 * for CSP found nothing. There is therefore no `frame-src`/`sandbox` directive
 * that would block a same-document sandboxed iframe. So we take the SAFE-but-
 * richer option: a sandboxed `<iframe srcdoc>` preview with the `sandbox`
 * attribute set to the EMPTY string — which disables scripts, forms, popups,
 * same-origin access, top-navigation, everything. (NO `allow-scripts`: the
 * agent's HTML output is untrusted, so no JS ever runs.)
 *
 * The preview is collapsed by default (P3 — hide machinery for non-technical
 * users); a toggle switches between the rendered Preview and the escaped HTML
 * Source (a normal code pane with Copy). Large-omitted bodies show the
 * standard "View full" stub.
 */
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { ToolEnvelopeOutput } from './parseEnvelope';
import { CopyButton } from './CopyButton';
import { ViewFullPanel } from './ViewFullPanel';

export interface HtmlPreviewProps {
  output: ToolEnvelopeOutput;
  abstract: string;
  status: 'success' | 'error';
  wfId: string | undefined;
}

export function HtmlPreview({ output, wfId }: HtmlPreviewProps) {
  const { t } = useTranslation();
  const data = typeof output.data === 'string' ? output.data : undefined;
  const [mode, setMode] = useState<'preview' | 'source'>('source');

  return (
    <div className="space-y-1 rounded-md border bg-muted/30" data-role="html-preview">
      <div className="flex items-center gap-2 border-b bg-muted/50 px-2 py-1 text-xs">
        <span className="font-semibold text-muted-foreground">
          {t('tool.html_output')}
        </span>
        <span className="flex-1" />
        {data !== undefined && (
          <>
            <button
              type="button"
              onClick={() => setMode((m) => (m === 'preview' ? 'source' : 'preview'))}
              data-action="html-toggle"
              className="rounded px-1.5 py-0.5 font-medium text-muted-foreground hover:bg-accent/60 hover:text-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
              aria-pressed={mode === 'preview'}
            >
              {mode === 'preview' ? t('tool.html_show_source') : t('tool.html_preview')}
            </button>
            <CopyButton value={data} />
          </>
        )}
      </div>
      {data !== undefined ? (
        mode === 'preview' ? (
          <iframe
            // sandbox="" disables ALL capabilities (no scripts, no same-origin,
            // no forms/popups/top-nav). The HTML is rendered as inert markup.
            sandbox=""
            srcDoc={data}
            title={t('tool.html_preview')}
            className="h-64 w-full rounded-b-md border-0 bg-white"
            data-role="html-iframe"
          />
        ) : (
          <pre
            className="max-h-72 overflow-auto whitespace-pre-wrap break-words rounded-b-md bg-muted/40 p-2 font-mono text-xs leading-snug"
            data-role="html-source"
          >
            {data}
          </pre>
        )
      ) : (
        <div
          className="p-2 text-xs text-muted-foreground"
          data-role="large-output-stub"
        >
          {t('tool.large_output')}
        </div>
      )}
      <div className="px-2 pb-1">
        <ViewFullPanel
          wfId={wfId}
          path={output.path}
          render={(content) => (
            <pre className="max-h-96 overflow-auto whitespace-pre-wrap break-words rounded border bg-muted/40 p-2 font-mono text-xs leading-snug">
              {content}
            </pre>
          )}
        />
      </div>
    </div>
  );
}
