/**
 * Terminal renderer for `text/shell` output.
 *
 * Dark mono pane (fixed dark surface independent of the app theme, like a
 * real terminal). Exit-code dot, stdout from `output.data`, dimmed stderr,
 * Copy, View-full. When `output.data` is absent (large-omitted) shows a
 * "Large output — View full" stub.
 *
 * The backend already produces a head+tail body with an inline `…elided…`
 * notice when the output was large; we render it as-is (no re-elision).
 */
import { useTranslation } from 'react-i18next';
import { cn } from '@/lib/utils';
import type { ToolEnvelopeOutput } from './parseEnvelope';
import { CopyButton } from './CopyButton';
import { ViewFullPanel } from './ViewFullPanel';
import { exitCodeTone } from './renderer-utils';

export interface TerminalBlockProps {
  output: ToolEnvelopeOutput;
  abstract: string;
  status: 'success' | 'error';
  wfId: string | undefined;
}

/**
 * Pure exit-code → dot color decision (exported for unit testing).
 *
 *   exit 0                       → green
 *   exit ≠ 0                     → red
 *   undefined exit + status ok   → green (success with no code, e.g. no-op)
 *   undefined exit + status err  → red
 */
export function TerminalBlock({ output, status, wfId }: TerminalBlockProps) {
  const { t } = useTranslation();
  const data = typeof output.data === 'string' ? output.data : undefined;
  const stderr =
    typeof output.stderr === 'string' && output.stderr.length > 0
      ? output.stderr
      : undefined;
  const tone = exitCodeTone(output.exit_code, status);
  const copyValue = [data, stderr].filter(Boolean).join('\n');

  return (
    <div
      className="overflow-hidden rounded-md border border-zinc-700 bg-zinc-950 text-zinc-100"
      data-role="terminal-block"
    >
      <div className="flex items-center gap-2 border-b border-zinc-800 px-2 py-1 text-xs">
        <span
          className={cn(
            'inline-block h-2 w-2 rounded-full',
            tone === 'green' ? 'bg-state-success' : 'bg-state-danger',
          )}
          data-role="exit-dot"
          data-tone={tone}
          aria-hidden="true"
        />
        <span className="text-zinc-400">
          {t('tool.exit_code')}:{' '}
          {typeof output.exit_code === 'number' ? output.exit_code : '—'}
        </span>
        {typeof output.duration_ms === 'number' && (
          <span className="text-zinc-500">{output.duration_ms}ms</span>
        )}
        <span className="flex-1" />
        {copyValue.length > 0 && (
          <CopyButton
            value={copyValue}
            label={t('tool.copyOutput', 'Copy output')}
            className="text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100"
          />
        )}
      </div>
      <div className="p-2 font-mono text-xs leading-snug">
        {typeof output.command === 'string' && output.command && (
          <pre className="mb-2 whitespace-pre-wrap break-words text-zinc-300">
            <span className="select-none text-emerald-400">$ </span>{output.command}
          </pre>
        )}
        {data !== undefined ? (
          <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-words">
            {data}
          </pre>
        ) : (
          <div className="text-zinc-400" data-role="large-output-stub">
            {t('tool.large_output')}
          </div>
        )}
        {stderr && (
          <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap break-words text-zinc-400">
            {stderr}
          </pre>
        )}
      </div>
      <div className="px-2 pb-1">
        <ViewFullPanel
          wfId={wfId}
          path={output.path}
          render={(content) => (
            <pre className="max-h-96 overflow-auto whitespace-pre-wrap break-words rounded bg-zinc-900 p-2 font-mono text-xs leading-snug text-zinc-100">
              {content}
            </pre>
          )}
        />
      </div>
    </div>
  );
}
