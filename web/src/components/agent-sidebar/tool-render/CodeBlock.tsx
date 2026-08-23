/**
 * Syntax-highlighted code renderer (e.g. `text/python`).
 *
 * Uses Shiki (via the minimal streaming-safe `useShiki` hook) to syntax-
 * highlight on a background async pass. The hook returns highlighted HTML
 * once ready; until then (and on any failure) it returns `null`, so we fall
 * back to a plain `<pre>` — render is NEVER blocked while Shiki loads.
 *
 * Header: language badge + path basename + Copy. Large-omitted → stub +
 * View-full.
 */
import { useTranslation } from 'react-i18next';
import { useShiki } from '@/lib/use-shiki';
import type { ToolEnvelopeOutput } from './parseEnvelope';
import { CopyButton } from './CopyButton';
import { ViewFullPanel } from './ViewFullPanel';

export interface CodeBlockProps {
  output: ToolEnvelopeOutput;
  abstract: string;
  status: 'success' | 'error';
  wfId: string | undefined;
  /** Highlight language (e.g. 'python'). */
  lang: string;
}

function basename(path: string | undefined): string | undefined {
  if (!path) return undefined;
  const parts = path.split('/').filter(Boolean);
  return parts.length ? parts[parts.length - 1] : path;
}

function Highlighted({ code, lang }: { code: string; lang: string }) {
  // Highlighted HTML once the async highlighter is ready; null meanwhile.
  const html = useShiki(code, lang);

  if (!html) {
    // Fallback while loading / on failure — never block render.
    return (
      <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-words rounded-b-md bg-muted/40 p-2 font-mono text-xs leading-snug">
        {code}
      </pre>
    );
  }
  return (
    <div
      className="max-h-72 overflow-auto rounded-b-md text-xs leading-snug [&_pre]:!m-0 [&_pre]:!bg-transparent [&_pre]:p-2"
      // Shiki output is generated from the escaped source code, not arbitrary
      // user HTML — safe to inject. See useShiki.ts.
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}

export function CodeBlock({ output, lang, wfId }: CodeBlockProps) {
  const { t } = useTranslation();
  const data = typeof output.data === 'string' ? output.data : undefined;
  const file = basename(output.path);

  return (
    <div
      className="overflow-hidden rounded-md border bg-muted/30"
      data-role="code-block"
      data-lang={lang}
    >
      <div className="flex items-center gap-2 border-b bg-muted/50 px-2 py-1 text-xs">
        <span className="rounded bg-background px-1.5 py-0.5 font-mono font-semibold text-muted-foreground">
          {lang}
        </span>
        {file && (
          <span className="truncate font-mono text-muted-foreground">{file}</span>
        )}
        <span className="flex-1" />
        {data !== undefined && <CopyButton value={data} />}
      </div>
      {data !== undefined ? (
        <Highlighted code={data} lang={lang} />
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
          render={(content) => <Highlighted code={content} lang={lang} />}
        />
      </div>
    </div>
  );
}
