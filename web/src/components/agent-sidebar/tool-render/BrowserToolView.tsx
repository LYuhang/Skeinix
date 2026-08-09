/**
 * Dedicated renderings for `browser_*` tools. Dispatched from `ToolCallBlock`
 * when `call.name` starts with
 * `browser_`; falls back to the generic `EnvelopeView` for anything it does
 * not specialise.
 *
 * Envelope shape (api/.../tools/browser_tools.py + _envelope.py):
 *   { status, error, abstract, output } where `output` is the raw observation
 *   dict the command returned, e.g.
 *     - navigate    → { ok, data: { final_url, title } }
 *     - snapshot    → { ok, data: { text, elements, … } }
 *     - read_text   → { ok, data: { text } }
 *     - read_fields → { ok, data: { fields: { name: value } } }
 *     - screenshot  → { ok, media: [ { path } ] }   (VFS PATH, never bytes)
 *     - get_image   → { ok, media: [ { path } ] }
 *     - click/type/submit/… → { ok, … }   (the acted element + post-condition
 *       live in the CALL ARGUMENTS — handle/selector + purpose/expect — not the
 *       result, so we read them from `arguments`).
 *
 * Media is rendered from the VFS path via `useSignedMediaSrc` — the SAME signed-
 * URL path the canvas template preview uses (`POST /vfs/sign` → a short-lived
 * `<img src>`-able URL). We NEVER read bytes inline (design global constraint).
 */
import { useTranslation } from 'react-i18next';
import { ExternalLink } from 'lucide-react';
import { useSignedMediaSrc } from '@/pages/canvas/nodes/template-preview-media';
import { EnvelopeView } from './EnvelopeView';
import type { ToolEnvelope, ToolEnvelopeOutput } from './parseEnvelope';

export interface BrowserToolViewProps {
  /** Tool name (e.g. `browser_navigate`). */
  toolName: string;
  /** Parsed result envelope. */
  envelope: ToolEnvelope;
  /** Raw `arguments` JSON string (acted element / purpose / expect live here). */
  arguments?: string;
  /** VFS scope id used to sign chat workspace media (`/data/browser-media/...`). */
  wfId?: string;
}

function isRecord(x: unknown): x is Record<string, unknown> {
  return typeof x === 'object' && x !== null;
}

function asString(x: unknown): string | undefined {
  return typeof x === 'string' && x.length > 0 ? x : undefined;
}

/** Pull `output.data` (the per-command observation payload) as a record. */
function readData(output: ToolEnvelopeOutput | undefined): Record<string, unknown> {
  const data = (output as Record<string, unknown> | undefined)?.data;
  return isRecord(data) ? data : {};
}

/** Pull the first `output.media[].path` (the screenshot/image VFS path). */
function readMediaPath(output: ToolEnvelopeOutput | undefined): string | undefined {
  const media = (output as Record<string, unknown> | undefined)?.media;
  if (!Array.isArray(media)) return undefined;
  for (const m of media) {
    if (isRecord(m)) {
      const p = asString(m.path);
      if (p) return p;
    }
  }
  return undefined;
}

/** Parse the call `arguments` JSON (fail-soft → {}). */
function parseArgs(raw: string | undefined): Record<string, unknown> {
  if (!raw) return {};
  try {
    const v = JSON.parse(raw);
    return isRecord(v) ? v : {};
  } catch {
    return {};
  }
}

/** Small labelled key/value row used by the structured renderings. */
function KeyValue({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex gap-2 text-xs">
      <span className="shrink-0 font-semibold text-muted-foreground">{label}</span>
      <span className="min-w-0 break-words">{children}</span>
    </div>
  );
}

/** A bounded, always-shown monospace text block (snapshot / read_text). NOT a
 *  second collapse — the whole tool block already collapses; long text scrolls. */
function BoundedText({ title, text }: { title: string; text: string }) {
  return (
    <div className="text-xs">
      <div className="font-semibold text-muted-foreground">{title}</div>
      <pre className="mt-1 max-h-72 overflow-auto whitespace-pre-wrap break-words rounded border bg-muted/40 p-2 font-mono leading-snug">
        {text}
      </pre>
    </div>
  );
}

/** Screenshot / get_image → a signed `<img>` from the media VFS path. */
function MediaImage({ path, wfId, alt }: { path: string; wfId?: string; alt: string }) {
  const { t } = useTranslation();
  const src = useSignedMediaSrc(path, { wfId });
  if (!src) {
    return (
      <div className="text-xs text-muted-foreground" data-role="browser-image-loading">
        {t('browser.image_loading', 'Loading image…')}
      </div>
    );
  }
  return (
    <a href={src} target="_blank" rel="noreferrer" data-role="browser-image">
      <img
        src={src}
        alt={alt}
        className="max-h-72 w-auto max-w-full rounded border"
        loading="lazy"
      />
    </a>
  );
}

export function BrowserToolView({
  toolName,
  envelope,
  arguments: rawArgs,
  wfId,
}: BrowserToolViewProps) {
  const { t } = useTranslation();

  // Error envelopes reuse the generic ErrorCard via EnvelopeView (it short-
  // circuits on status === 'error'), so soft-errors (no_browser, browser_error)
  // get the consistent "ask the agent to fix" affordance.
  if (envelope.status === 'error') {
    return (
      <EnvelopeView
        output={envelope.output}
        abstract={envelope.abstract}
        status={envelope.status}
        wfId={wfId}
        error={envelope.error}
        toolName={toolName}
      />
    );
  }

  const data = readData(envelope.output);
  const args = parseArgs(rawArgs);

  switch (toolName) {
    // ── Navigation: final URL + resolved title ────────────────────────────
    case 'browser_navigate': {
      const finalUrl = asString(data.final_url) ?? asString(args.url);
      const title = asString(data.title);
      return (
        <div className="space-y-1" data-role="browser-navigate">
          {title && <KeyValue label={t('browser.title', 'Title')}>{title}</KeyValue>}
          {finalUrl && (
            <KeyValue label={t('browser.url', 'URL')}>
              <a
                href={finalUrl}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-1 text-primary hover:underline"
              >
                <span className="break-all">{finalUrl}</span>
                <ExternalLink className="h-3 w-3 shrink-0" />
              </a>
            </KeyValue>
          )}
          {!title && !finalUrl && (
            <div className="text-xs text-muted-foreground">{envelope.abstract}</div>
          )}
        </div>
      );
    }

    // ── Pixels: signed <img> from the media VFS path ─────────────────────
    // browser_take_screenshot covers both whole-page and per-element capture
    // (the latter via its optional `handle`, folding the old browser_get_image).
    case 'browser_take_screenshot': {
      const path = readMediaPath(envelope.output);
      if (!path) {
        return (
          <div className="text-xs text-muted-foreground">{envelope.abstract}</div>
        );
      }
      return (
        <div className="space-y-1" data-role="browser-media">
          <MediaImage path={path} wfId={wfId} alt={toolName} />
          <div className="truncate text-xs text-muted-foreground" title={path}>
            {path}
          </div>
        </div>
      );
    }

    // ── Page reads: collapsible text / fields ────────────────────────────
    case 'browser_snapshot':
    case 'browser_read_text': {
      const text = asString(data.text);
      if (text) {
        return (
          <BoundedText
            title={t('browser.page_text', 'Page text')}
            text={text}
          />
        );
      }
      // Snapshot may carry a structured tree with no flat `text` — fall through
      // to the generic envelope view so the JSON is still inspectable.
      break;
    }

    case 'browser_read_fields': {
      const fields = isRecord(data.fields) ? data.fields : undefined;
      if (fields) {
        const entries = Object.entries(fields);
        return (
          <div className="space-y-1" data-role="browser-fields">
            {entries.length === 0 && (
              <div className="text-xs text-muted-foreground">
                {t('browser.no_fields', 'No fields read.')}
              </div>
            )}
            {entries.map(([name, value]) => (
              <KeyValue key={name} label={name}>
                {typeof value === 'string' ? value : JSON.stringify(value)}
              </KeyValue>
            ))}
          </div>
        );
      }
      break;
    }

    // ── Actions: acted element + expected post-condition ─────────────────
    case 'browser_click':
    case 'browser_submit':
    case 'browser_type':
    case 'browser_fill':
    case 'browser_select_option':
    case 'browser_press_key': {
      const acted =
        asString(args.handle) ??
        asString(args.selector) ??
        asString(args.key) ??
        asString(args.option);
      const purpose = asString(args.purpose);
      const expect = asString(args.expect);
      const typedText = asString(args.text);
      const hasAny = acted || purpose || expect || typedText;
      return (
        <div className="space-y-1" data-role="browser-action">
          {acted && <KeyValue label={t('browser.element', 'Element')}>{acted}</KeyValue>}
          {typedText && <KeyValue label={t('browser.text', 'Text')}>{typedText}</KeyValue>}
          {purpose && <KeyValue label={t('browser.purpose', 'Purpose')}>{purpose}</KeyValue>}
          {expect && <KeyValue label={t('browser.expect', 'Expect')}>{expect}</KeyValue>}
          {!hasAny && (
            <div className="text-xs text-muted-foreground">{envelope.abstract}</div>
          )}
        </div>
      );
    }

    default:
      break;
  }

  // Anything not specialised above → the generic envelope renderer.
  return (
    <EnvelopeView
      output={envelope.output}
      abstract={envelope.abstract}
      status={envelope.status}
      wfId={wfId}
      error={envelope.error}
      toolName={toolName}
    />
  );
}
