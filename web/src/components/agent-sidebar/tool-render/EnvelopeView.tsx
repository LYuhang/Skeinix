/**
 * Dispatches a parsed tool envelope's `output` to the right renderer by
 * `content_type` (lowercased). F1 covers shell / python / markdown, with a
 * `text/plain`/unknown fallback to `TextBlock`. Later phases (F2) add
 * JSON / table / html / link / error renderers; until then they also render
 * via `TextBlock` (fail-soft — never crash, always show *something* readable).
 *
 * When the envelope has no `output` at all (e.g. a bare success/error with
 * just an abstract), we render the abstract as plain text.
 */
import { useTranslation } from 'react-i18next';
import { Markdown } from '../Markdown';
import type { ToolEnvelope } from './parseEnvelope';
import { TerminalBlock } from './TerminalBlock';
import { CodeBlock } from './CodeBlock';
import { TextBlock } from './TextBlock';
import { TableView } from './TableView';
import { JsonTree } from './JsonTree';
import { HtmlPreview } from './HtmlPreview';
import { LinkCard } from './LinkCard';
import { ErrorCard } from './ErrorCard';
import { ViewFullPanel } from './ViewFullPanel';
import { rendererFor } from './renderer-utils';
import { DiffBlock } from './DiffBlock';

export interface EnvelopeViewProps {
  output: ToolEnvelope['output'];
  abstract: string;
  status: 'success' | 'error';
  wfId: string | undefined;
  /** Raw error string (when `status === 'error'`) → ErrorCard. */
  error?: string | null;
  /** Tool name (for the ErrorCard "ask to fix" prefill). */
  toolName?: string;
  onOpenFile?: (path: string) => void;
}

/**
 * Pure mapping of a (lowercased) content_type to a renderer key — exported
 * for unit testing the dispatch table without rendering React. Note: the
 * `status === 'error'` short-circuit to the `ErrorCard` is handled in
 * `EnvelopeView` BEFORE this content_type dispatch (an error envelope often
 * has no/irrelevant content_type), so `rendererFor` never returns 'error'.
 */
export function EnvelopeView({
  output,
  abstract,
  status,
  wfId,
  error,
  toolName,
  onOpenFile,
}: EnvelopeViewProps) {
  // Error envelopes get the ErrorCard regardless of content_type (§7).
  if (status === 'error') {
    return <ErrorCard error={error ?? null} abstract={abstract} toolName={toolName} />;
  }

  if (!output) {
    return (
      <div className="text-xs text-muted-foreground" data-role="envelope-abstract">
        {abstract}
      </div>
    );
  }

  const kind = rendererFor(output.content_type);

  switch (kind) {
    case 'terminal':
      return (
        <TerminalBlock
          output={output}
          abstract={abstract}
          status={status}
          wfId={wfId}
        />
      );
    case 'diff':
      return <DiffBlock diff={typeof output.data === 'string' ? output.data : ''} path={output.path} onOpenFile={onOpenFile} />;
    case 'code':
      return (
        <CodeBlock
          output={output}
          abstract={abstract}
          status={status}
          wfId={wfId}
          lang="python"
        />
      );
    case 'markdown':
      return <MarkdownBranch output={output} wfId={wfId} />;
    case 'table':
      return (
        <TableView output={output} abstract={abstract} status={status} wfId={wfId} />
      );
    case 'json':
      return (
        <JsonTree output={output} abstract={abstract} status={status} wfId={wfId} />
      );
    case 'html':
      return (
        <HtmlPreview output={output} abstract={abstract} status={status} wfId={wfId} />
      );
    case 'link':
      return (
        <LinkCard output={output} abstract={abstract} status={status} wfId={wfId} />
      );
    case 'text':
    default:
      return (
        <TextBlock output={output} abstract={abstract} status={status} wfId={wfId} />
      );
  }
}

/** Markdown body in a bounded scroll window; large-omitted bodies load from VFS. */
function MarkdownBranch({
  output,
  wfId,
}: {
  output: ToolEnvelope['output'];
  wfId: string | undefined;
}) {
  const { t } = useTranslation();
  const data = output?.data;
  if (typeof data === 'string') {
    return (
      <div
        data-role="markdown-block"
        className="max-h-96 overflow-auto rounded border bg-muted/20 p-2"
      >
        <Markdown>{data}</Markdown>
      </div>
    );
  }
  // Omitted (large) → load the full body from VFS into the bounded scroll panel.
  return (
    <div>
      <div
        className="rounded border bg-muted/30 p-2 text-xs text-muted-foreground"
        data-role="large-output-stub"
      >
        {t('tool.large_output')}
      </div>
      <ViewFullPanel
        wfId={wfId}
        path={output?.path}
        render={(c) => (
          <div className="max-h-96 overflow-auto rounded border bg-muted/20 p-2">
            <Markdown>{c}</Markdown>
          </div>
        )}
      />
    </div>
  );
}
