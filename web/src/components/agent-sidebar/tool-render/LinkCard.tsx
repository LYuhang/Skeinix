/**
 * Link renderer for `link/cloud_table` and other `link/*` content types
 * Shows a titled card with an external-link button that opens
 * the target in a new tab. NO inline fetch — the link is a handle, not a
 * preview (the target may be a large cloud resource).
 *
 * The URL is taken from `output.url` when present, else `output.path` (the
 * envelope's generic handle field). The title prefers `abstract`.
 *
 * Fail-soft: when there is no openable URL, the button is omitted and only
 * the abstract is shown.
 */
import { ExternalLink } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import type { ToolEnvelopeOutput } from './parseEnvelope';

export interface LinkCardProps {
  output: ToolEnvelopeOutput;
  abstract: string;
  status: 'success' | 'error';
  wfId: string | undefined;
}

/** True only for http(s) URLs we are willing to open in a new tab. */
function isHttpUrl(s: string | undefined): s is string {
  if (typeof s !== 'string') return false;
  return /^https?:\/\//i.test(s.trim());
}

export function LinkCard({ output, abstract }: LinkCardProps) {
  const { t } = useTranslation();
  const urlField = typeof output.url === 'string' ? output.url : undefined;
  const url = isHttpUrl(urlField)
    ? urlField
    : isHttpUrl(output.path)
      ? output.path
      : undefined;
  const title = (abstract && abstract.trim()) || t('tool.link_default_title');

  return (
    <div
      className="flex items-center gap-2 rounded-md border bg-muted/30 p-2 text-xs"
      data-role="link-card"
    >
      <ExternalLink
        className="h-3.5 w-3.5 shrink-0 text-muted-foreground"
        aria-hidden="true"
      />
      <span className="min-w-0 flex-1 truncate font-medium">{title}</span>
      {url && (
        <a
          href={url}
          target="_blank"
          rel="noopener noreferrer"
          data-action="link-open"
          className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 font-medium text-primary hover:underline focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
        >
          {t('tool.open_link')}
          <ExternalLink className="h-3 w-3" />
        </a>
      )}
    </div>
  );
}
