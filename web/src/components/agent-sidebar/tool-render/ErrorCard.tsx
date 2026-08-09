/**
 * Error renderer for `envelope.status === "error"`.
 *
 * Non-technical-user friendly (P6): we NEVER dump a raw stack trace. The
 * `envelope.error` string is mapped to a small set of plain-language
 * categories (timeout / not-found / permission / bad-input); anything we
 * cannot classify shows the raw error in a single muted mono line (one line —
 * not a multi-line trace dump), preferring the envelope's `abstract` headline
 * when present.
 *
 * One-click **"Ask the agent to fix this"** prefills a follow-up user message
 * into the composer (via the chat-stream store `setDraft`, consumed by
 * `ChatComposer`). A "Copy error" fallback is always available.
 *
 * Fail-soft: pure category mapping (`classifyError`) never throws; an empty /
 * missing error still renders a generic friendly line.
 */
import { AlertTriangle } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { CopyButton } from './CopyButton';
import { useChatStreamStore } from '@/stores/chat-stream';
import { useChatRenderIdentity } from '../chat-render-context';
import { classifyError, type ErrorCategory } from './renderer-utils';

export interface ErrorCardProps {
  error: string | null;
  abstract: string;
  /** Tool name — included in the "ask to fix" prefill for the agent's benefit. */
  toolName?: string;
}

/**
 * Plain-language error categories. `unknown` carries the raw error through to
 * the UI (shown as a single muted mono line), every other category maps to a
 * friendly i18n key with NO raw text leaked.
 */
/**
 * Classify a raw tool error string into a friendly category (pure; exported
 * for unit testing). Matching is lowercase substring heuristics over the
 * common phrasings emitted by the backend / OS / Python — deliberately
 * conservative: an unrecognised error falls through to `unknown` (raw shown).
 */
/** i18n key for a category's friendly line (excludes `unknown` → raw shown). */
const CATEGORY_KEY: Record<Exclude<ErrorCategory, 'unknown'>, string> = {
  timeout: 'error.category.timeout',
  not_found: 'error.category.not_found',
  permission: 'error.category.permission',
  bad_input: 'error.category.bad_input',
};

export function ErrorCard({ error, abstract, toolName }: ErrorCardProps) {
  const { t } = useTranslation();
  const setDraft = useChatStreamStore((s) => s.setDraft);
  const activeChatId = useChatRenderIdentity()?.chatId ?? null;

  const category = classifyError(error);
  // Headline: friendly category line, falling back to the abstract, then a
  // generic "the tool failed" line. Never the raw error here.
  const headline =
    category !== 'unknown'
      ? t(CATEGORY_KEY[category])
      : abstract && abstract.trim()
        ? abstract
        : t('error.generic');

  // Raw line: only for `unknown` (single muted mono line, never a trace dump).
  const rawLine = category === 'unknown' && error ? error : undefined;

  const askToFix = () => {
    const where = toolName ? ` (\`${toolName}\`)` : '';
    const detail = error ?? abstract ?? '';
    setDraft(t('error.ask_to_fix_prefill', { where, detail }), activeChatId);
  };

  return (
    <div
      className="space-y-1.5 rounded-md border border-destructive/40 bg-destructive/5 p-2 text-xs"
      data-role="error-card"
      data-error-category={category}
    >
      <div className="flex items-start gap-1.5">
        <AlertTriangle
          className="mt-0.5 h-3.5 w-3.5 shrink-0 text-destructive"
          aria-hidden="true"
        />
        <span className="font-medium text-destructive">{headline}</span>
      </div>
      {rawLine && (
        <pre
          className="overflow-x-auto whitespace-pre-wrap break-words rounded bg-muted/40 px-1.5 py-1 font-mono text-xs text-muted-foreground"
          data-role="error-raw"
        >
          {rawLine}
        </pre>
      )}
      <div className="flex items-center gap-1">
        <button
          type="button"
          onClick={askToFix}
          data-action="error-ask-to-fix"
          className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium text-primary hover:underline focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
        >
          {t('error.ask_to_fix')}
        </button>
        {(error || abstract) && (
          <CopyButton value={error || abstract} />
        )}
      </div>
    </div>
  );
}
