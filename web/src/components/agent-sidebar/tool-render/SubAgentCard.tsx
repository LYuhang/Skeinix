/**
 * Sub-agent card for the `run_subagent` tool.
 *
 *   ┌──────────────────────────────────────────┐
 *   │ ▸ 🤖 Sub-agent: summarise the rules  ✓     │   ← collapsed (objective + status)
 *   ├──────────────────────────────────────────┤
 *   │ <structured set_output result>             │   ← expanded
 *   └──────────────────────────────────────────┘
 *
 * `run_subagent` returns a STRUCTURED dict — the worker's terminal `set_output`
 * result plus a summary — NOT the inner transcript
 * (`api/.../agents/subagent_tool.py`):
 *
 *   { "status": "success"|"error", "output": {<set_output fields>},
 *     "reasoning_ref": "/run/__exec__/subagents/<id>.jsonl", "error": null|"…" }
 *
 * There is no recursive inner timeline; the card renders the
 * structured `output` only:
 *   - if `output` looks like a tool ENVELOPE (`{status, output:{content_type,…}}`)
 *     we route it through the existing `EnvelopeView` renderer family;
 *   - else we show it as a `JsonTree` (structured dict) or a `TextBlock`
 *     (string), with the worker's error surfaced when `status === 'error'`.
 *
 * Fail-soft: a result that does not parse into a recognisable sub-agent dict
 * makes `subAgentFromResult` return `null`, so `ToolCallBlock` falls back to
 * the generic envelope/legacy chip — this component never throws.
 */
import { useState } from 'react';
import { Bot, CheckCircle2, ChevronDown, ChevronRight, XCircle } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { cn } from '@/lib/utils';
import type { ToolEnvelopeOutput } from './parseEnvelope';
import { EnvelopeView } from './EnvelopeView';
import { JsonTree } from './JsonTree';
import { TextBlock } from './TextBlock';
import { type SubAgentResult } from './renderer-utils';

/** Normalised view of a `run_subagent` result (pure; exported for tests). */
function isObject(x: unknown): x is Record<string, unknown> {
  return typeof x === 'object' && x !== null;
}

/**
 * Parse a `run_subagent` tool result string into a `SubAgentResult`, or `null`
 * when it is not a recognisable sub-agent result (fail-soft → legacy chip).
 *
 * Recognition: a JSON object carrying a `status` of `success`/`error` AND an
 * `output` key (the structured set_output result). This deliberately differs
 * from a plain tool envelope only by always expecting `output` — but since we
 * only call this for `call.name === 'run_subagent'`, a tolerant shape is fine.
 */
/** True when a structured output is itself a tool envelope (route via EnvelopeView). */
function looksLikeEnvelope(output: unknown): output is Record<string, unknown> {
  return (
    isObject(output) &&
    (output.status === 'success' || output.status === 'error') &&
    isObject(output.output)
  );
}

function StatusIcon({ status }: { status: 'success' | 'error' }) {
  if (status === 'error') {
    return (
      <XCircle className="h-3.5 w-3.5 shrink-0 text-state-danger" aria-label="error" />
    );
  }
  return (
    <CheckCircle2
      className="h-3.5 w-3.5 shrink-0 text-state-success"
      aria-label="done"
    />
  );
}

/** Render the structured set_output `output` body (no inner transcript). */
function SubAgentBody({
  result,
  abstract,
  wfId,
}: {
  result: SubAgentResult;
  abstract: string;
  wfId: string | undefined;
}) {
  const { output } = result;

  // 1) The structured output is itself a tool envelope → reuse the renderers.
  if (looksLikeEnvelope(output)) {
    return (
      <EnvelopeView
        output={output.output as ToolEnvelopeOutput}
        abstract={typeof output.abstract === 'string' ? output.abstract : abstract}
        status={output.status as 'success' | 'error'}
        wfId={wfId}
        error={typeof output.error === 'string' ? output.error : null}
        toolName="run_subagent"
      />
    );
  }

  // 2) A plain string result → TextBlock.
  if (typeof output === 'string') {
    return (
      <TextBlock
        output={{ content_type: 'text/plain', data: output }}
        abstract={abstract}
        status={result.status}
        wfId={wfId}
      />
    );
  }

  // 3) A structured dict/array → JsonTree (the common set_output case).
  if (isObject(output) || Array.isArray(output)) {
    return (
      <JsonTree
        output={{ content_type: 'application/json', data: JSON.stringify(output) }}
        abstract={abstract}
        status={result.status}
        wfId={wfId}
      />
    );
  }

  // 4) Nothing renderable (null/undefined output) → the abstract line.
  return (
    <div className="text-xs text-muted-foreground" data-role="subagent-abstract">
      {abstract}
    </div>
  );
}

export interface SubAgentCardProps {
  result: SubAgentResult;
  /** Plain-language one-liner from the wrapping envelope abstract (if any). */
  abstract: string;
  /** Force open by default (while the parent turn streams). */
  autoExpand?: boolean;
  wfId?: string;
}

export function SubAgentCard({ result, abstract, autoExpand, wfId }: SubAgentCardProps) {
  const { t } = useTranslation();
  const [open, setOpen] = useState<boolean>(!!autoExpand);
  const Chevron = open ? ChevronDown : ChevronRight;

  // Collapsed headline: the abstract (objective summary) when present, else a
  // generic label. Friendly, never the raw structured dict.
  const headline = (abstract && abstract.trim()) || t('subagent.label');

  return (
    <div
      className="rounded-md border bg-background/60 text-xs"
      data-role="subagent-card"
      data-subagent-status={result.status}
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className={cn('flex w-full items-center gap-2 px-2 py-1.5 text-left', 'hover:bg-accent/50')}
        aria-expanded={open}
        data-action="subagent-toggle"
      >
        <Chevron className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
        <Bot className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
        <span className="flex-1 truncate text-xs font-semibold">{headline}</span>
        <StatusIcon status={result.status} />
      </button>

      {open && (
        <div className="space-y-2 border-t px-2 py-2" data-role="subagent-body">
          {result.status === 'error' && result.error && (
            <div className="text-xs text-state-danger" data-role="subagent-error">
              {result.error}
            </div>
          )}
          <SubAgentBody result={result} abstract={abstract} wfId={wfId} />
        </div>
      )}
    </div>
  );
}
