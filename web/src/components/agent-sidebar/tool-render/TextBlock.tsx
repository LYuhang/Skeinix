/**
 * Plain-text / unknown-content_type renderer (the default branch of
 * `EnvelopeView`). Mono, scrollable, max-height window. Copy + View-full.
 *
 * When `output.data` is absent (large-omitted) it shows a "Large output —
 * View full" stub instead of an empty pane.
 */
import { useTranslation } from 'react-i18next';
import type { ToolEnvelopeOutput } from './parseEnvelope';
import { isLargeOmitted } from './parseEnvelope';
import { CopyButton } from './CopyButton';
import { ViewFullPanel } from './ViewFullPanel';

export interface TextBlockProps {
  output: ToolEnvelopeOutput;
  abstract: string;
  status: 'success' | 'error';
  wfId: string | undefined;
}

export function TextBlock({ output, wfId }: TextBlockProps) {
  const { t } = useTranslation();
  // Render any PRESENT body — a string as-is, an object (e.g. a browser tool's
  // {value}/{tabs}/{text} payload) as pretty JSON — in the bounded scroll window.
  // The "Large output" stub is only for a GENUINELY omitted body (the backend
  // dropped it inline and wrote it to VFS `path`, which ViewFullPanel loads).
  const raw = output.data;
  const omitted =
    (raw === undefined || raw === null) &&
    isLargeOmitted({ status: 'success', error: null, abstract: '', output });
  const text =
    raw === undefined || raw === null
      ? undefined
      : typeof raw === 'string'
        ? raw
        : JSON.stringify(raw, null, 2);

  return (
    <div className="space-y-1" data-role="text-block">
      <div className="flex items-center justify-end">
        {text !== undefined && <CopyButton value={text} />}
      </div>
      {text !== undefined ? (
        <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-words rounded border bg-muted/40 p-2 font-mono text-xs leading-snug">
          {text}
        </pre>
      ) : (
        omitted && (
          <div
            className="rounded border bg-muted/30 p-2 text-xs text-muted-foreground"
            data-role="large-output-stub"
          >
            {t('tool.large_output')}
          </div>
        )
      )}
      <ViewFullPanel wfId={wfId} path={output.path} />
    </div>
  );
}
