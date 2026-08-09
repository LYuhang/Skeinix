/**
 * Collapsible per-tool-call panel.
 *
 *   ┌────────────────────────────────────────────┐
 *   │ ▸ tool_name                           …    │   ← header (always visible)
 *   ├────────────────────────────────────────────┤
 *   │ args:   {"updates": [...]}                 │   ← body (when open)
 *   │ result: <!-- DIFF before → after -->       │
 *   │   + new node                               │
 *   │   - old node                               │
 *   └────────────────────────────────────────────┘
 *
 * Header status icon legend:
 *   - `…`  running  (animated ellipsis)
 *   - `✓`  done     (rendered implicitly via subtler chevron tone; we use
 *                    a CheckCircle from lucide instead of a glyph so themes
 *                    can recolor it consistently)
 *   - `✕`  error    (red XCircle)
 *
 * Default-open rule:
 *   - `autoExpand` (when passed by a streaming `MessageItem`) forces open
 *     so the user sees the call as it forms.
 *   - `status === 'running'` also forces open for the same reason — a
 *     persisted-history block that's still in flight (rare; would imply
 *     the page reloaded mid-turn) should be expanded.
 *   - Everything else starts collapsed; clicking the header toggles.
 *
 * Parsing notes:
 *   - Envelope results use dedicated renderers when available.
 *   - Non-envelope results fall through to the generic preformatted renderer.
 *     We never throw on parse mismatch.
 */
import { useRef, useState } from 'react';
import {
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Loader2,
  XCircle,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { cn } from '@/lib/utils';
import type { MergedToolCall } from './types';
import { parseArtifactEnvelope, parseEnvelope } from './tool-render/parseEnvelope';
import { EnvelopeView } from './tool-render/EnvelopeView';
import { BrowserToolView } from './tool-render/BrowserToolView';
import { getToolMeta } from './tool-render/toolMeta';
import { SubAgentCard } from './tool-render/SubAgentCard';
import { subAgentFromResult } from './tool-render/renderer-utils';
import { DiffBlock } from './tool-render/DiffBlock';
import { ToolArgumentsView } from './tool-render/ToolArgumentsView';
import { UniversalToolResult } from './tool-render/UniversalToolResult';
import { parseStandardToolResult } from './tool-render/parseStandardToolResult';
import { TerminalBlock } from './tool-render/TerminalBlock';
import { isTrustedToolPresentation, selectToolPresenter } from './tool-render/presenterRegistry';
import { RendererErrorBoundary } from '@/components/ui/renderer-error-boundary';

export interface ToolCallBlockProps {
  call: MergedToolCall;
  /** Force open by default (typically while the parent turn is streaming). */
  autoExpand?: boolean;
  /** Chat/session carrier id — kept for history-era callers. */
  wfId?: string;
  /** VFS scope used by renderers for `/data|/memory|/logs|/mount` paths. */
  vfsScopeId?: string;
  onOpenFilePreview?: (path: string) => void;
}

function originLabel(call: MergedToolCall): string {
  const origin = call.invocation?.origin;
  if (!origin) return '';
  if (typeof origin === 'string') return origin;
  if (origin.kind === 'custom_mcp' || origin.kind === 'platform_mcp') {
    return `${origin.serverLabel || origin.serverName || 'MCP'} MCP`;
  }
  return origin.provider || origin.kind;
}

function StatusIcon({ status }: { status: MergedToolCall['status'] }) {
  const { t } = useTranslation();
  if (status === 'running') {
    return (
      <Loader2
        className="h-3.5 w-3.5 animate-spin text-state-running motion-reduce:animate-none"
        aria-label={t('tool.status.running', 'Running')}
        role="status"
      />
    );
  }
  if (status === 'error') {
    return (
      <XCircle
        className="h-3.5 w-3.5 text-state-danger"
        aria-label={t('tool.status.error', 'Failed')}
        role="status"
      />
    );
  }
  return (
    <CheckCircle2
      className="h-3.5 w-3.5 text-state-success"
      aria-label={t('tool.status.done', 'Completed')}
      role="status"
    />
  );
}

export function ToolCallBlock({ call, autoExpand, wfId, vfsScopeId, onOpenFilePreview }: ToolCallBlockProps) {
  const { t } = useTranslation();
  const renderScopeId = vfsScopeId || wfId;
  const status: MergedToolCall['status'] = call.status;
  const [open, setOpen] = useState<boolean>(
    !!autoExpand || status === 'running',
  );
  // A call that opened while running stays open after completion so output does
  // not disappear while the user is reading it. Manual disclosure always wins.
  const userToggled = useRef(false);
  // Product-specific presenters are selected by a trusted, normalized origin.
  // A custom MCP can intentionally reuse a built-in name (for example `bash`),
  // so a raw name or a self-declared presentation hint is never sufficient.
  const trustedSemanticPresentation = isTrustedToolPresentation(call);

  // Structured sub-agent results use a dedicated card. A parse miss falls
  // through to the generic renderer so malformed output cannot break Chat.
  if (trustedSemanticPresentation && call.status !== 'running' && call.result) {
    if (call.name === 'run_subagent') {
      const sub = subAgentFromResult(call.result);
      if (sub) {
        const env = parseEnvelope(call.result);
        return (
          <div data-role="tool-call" data-tool-name={call.name} data-tool-status={call.status}>
            <SubAgentCard
              result={sub}
              abstract={env?.abstract ?? ''}
              autoExpand={autoExpand}
              wfId={renderScopeId}
            />
          </div>
        );
      }
    }
  }

  // Generic envelope (null for legacy plain-string results → legacy <pre>).
  const envelope = trustedSemanticPresentation
    ? parseEnvelope(call.result) ?? parseArtifactEnvelope(call.artifact)
    : null;
  const universal = envelope ? null : parseStandardToolResult(call.result);
  const presenter = selectToolPresenter({ call, envelope, hasUniversal: !!universal });

  const meta = getToolMeta(call.name);
  const MetaIcon = meta.icon;
  // Collapsed header label: prefer the envelope's plain-language abstract,
  // else the friendly tool label, else the raw tool name.
  const headerLabel =
    (envelope?.abstract && envelope.abstract.trim()) ||
    (meta.labelKey ? t(meta.labelKey) : call.name);
  const statusLabel = status === 'running'
    ? t('tool.status.running', 'Running')
    : status === 'error'
      ? t('tool.status.error', 'Failed')
      : t('tool.status.done', 'Completed');

  const Chevron = open ? ChevronDown : ChevronRight;
  return (
    <div
      className={cn(
        'text-xs transition-colors',
        status === 'error' && 'border-state-danger/30',
        open
          ? 'rounded-md border border-edge-subtle bg-surface-sunken/35'
          : 'rounded-md bg-transparent',
      )}
      data-role="tool-call"
      data-tool-name={call.name}
      data-tool-status={status}
      aria-label={`${call.name}: ${status}`}
    >
      <button
        type="button"
        onClick={() => {
          userToggled.current = true;
          setOpen((v) => !v);
        }}
        className={cn(
          'flex w-full items-center gap-2 rounded-md text-left transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring/30',
          open ? 'px-2 py-1.5 hover:bg-accent/50' : 'px-1.5 py-1.5 hover:bg-muted/35',
        )}
        aria-expanded={open}
        data-action="tool-call-toggle"
      >
        <Chevron className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
        <MetaIcon className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
        <span
          className={cn(
            'flex-1 truncate text-xs',
            open ? 'font-semibold text-foreground' : 'font-medium text-muted-foreground',
          )}
        >
          {headerLabel}
        </span>
        {call.invocation?.origin ? (
          <span className="max-w-28 shrink-0 truncate rounded-full border border-edge-subtle bg-surface-raised px-1.5 py-0.5 text-xs text-content-tertiary" title={`${originLabel(call)} · ${call.invocation.capability} · ${call.invocation.name}`}>
            {originLabel(call)}
          </span>
        ) : null}
        <span className={cn(
          'shrink-0 text-xs font-medium',
          status === 'running' && 'text-state-running',
          status === 'error' && 'text-state-danger',
          status === 'done' && 'text-state-success',
        )}>{statusLabel}</span>
        <StatusIcon status={status} />
      </button>

      {open && (
        <div className="space-y-2 border-t px-2 py-2">
          {call.invocation ? (
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-content-tertiary" data-role="tool-invocation-meta">
              <span>{call.invocation.runtime.type}</span>
              {call.invocation.nativeKind ? (
                <>
                  <span aria-hidden="true">·</span>
                  <span data-role="tool-native-kind">
                    {call.invocation.nativeKind}
                  </span>
                </>
              ) : null}
              <span aria-hidden="true">·</span>
              <span>{originLabel(call)}</span>
              <span aria-hidden="true">·</span>
              <span>{call.invocation.capability}</span>
              {typeof call.invocation.timing?.durationMs === 'number' ? <><span aria-hidden="true">·</span><span className="tabular-nums">{call.invocation.timing.durationMs} ms</span></> : null}
              {call.invocation.risk && call.invocation.risk !== 'unknown' ? <span className="rounded-full border border-edge-subtle px-1.5 py-0.5">{call.invocation.risk}</span> : null}
              {call.invocation.error?.retryable === true ? <span>{t('tool.retryable', 'retryable')}</span> : null}
            </div>
          ) : null}
          {/* ARGUMENTS — the call's INPUT, shown first. NOT a second collapse
              (the whole block already collapses) — just a bounded, scrollable
              window so long args don't dominate. */}
          <div className="text-xs" data-role="tool-args">
            <div className="text-xs font-medium text-muted-foreground">
              {t('tool.args')}
            </div>
            <div className="mt-1">
              <ToolArgumentsView
                toolName={trustedSemanticPresentation ? call.name : 'generic'}
                argumentsText={call.arguments}
              />
            </div>
          </div>

          {/* OUTPUT — the RESULT, shown after the arguments. Rendered whenever a
              result exists (NOT gated on a possibly stale `running` status: a
              result frame that didn't flip the status must still show output). */}
          {call.result !== undefined ? (
            <div data-role="tool-output">
              <div className="mb-1 text-xs font-medium text-muted-foreground">
                {t('tool.output', 'output')}
              </div>
              <RendererErrorBoundary
                resetKey={`${call.id}:${call.result?.length ?? 0}`}
                fallback={<pre className="max-h-72 overflow-auto whitespace-pre-wrap break-words rounded border bg-muted/40 p-2 font-mono text-xs leading-snug">{call.result}</pre>}
              >
              {presenter === 'terminal' && envelope?.output ? (
                <TerminalBlock
                  output={{
                    ...envelope.output,
                    command: typeof envelope.output.command === 'string'
                      ? envelope.output.command
                      : (() => {
                          try {
                            const value = JSON.parse(call.arguments) as { command?: unknown };
                            return typeof value.command === 'string' ? value.command : undefined;
                          } catch {
                            return undefined;
                          }
                        })(),
                    duration_ms: envelope.output.duration_ms ?? call.invocation?.timing?.durationMs,
                  }}
                  abstract={envelope.abstract}
                  status={envelope.status}
                  wfId={renderScopeId}
                />
              ) : presenter === 'diff' && envelope?.output && typeof envelope.output.data === 'string' ? (
                <DiffBlock
                  diff={envelope.output.data}
                  path={typeof envelope.output.path === 'string' ? envelope.output.path : undefined}
                  onOpenFile={onOpenFilePreview}
                />
              ) : presenter === 'browser' && envelope ? (
                // Browser tools get dedicated renderings (navigate URL,
                // screenshot <img>, page text, acted element + expect); the
                // view falls back to EnvelopeView for anything it doesn't
                // specialise.
                <BrowserToolView
                  toolName={call.name}
                  envelope={envelope}
                  arguments={call.arguments}
                  wfId={renderScopeId}
                />
              ) : presenter === 'envelope' && envelope ? (
                <EnvelopeView
                  output={envelope.output}
                  abstract={envelope.abstract}
                  status={envelope.status}
                  wfId={renderScopeId}
                  error={envelope.error}
                  toolName={call.name}
                  onOpenFile={onOpenFilePreview}
                />
              ) : presenter === 'universal' && universal ? (
                <UniversalToolResult
                  value={universal}
                  wfId={renderScopeId}
                  onOpenFile={onOpenFilePreview}
                />
              ) : (
                // Legacy / non-envelope result → original preformatted render.
                <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-words rounded border bg-muted/40 p-2 font-mono text-xs leading-snug">
                  {call.result}
                </pre>
              )}
              </RendererErrorBoundary>
            </div>
          ) : status === 'running' ? (
            <div className="text-xs italic text-muted-foreground">
              {t('tool.waiting', 'waiting for result…')}
            </div>
          ) : (
            <div className="text-xs italic text-muted-foreground">
              {t('tool.no_result', '(no result captured)')}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
