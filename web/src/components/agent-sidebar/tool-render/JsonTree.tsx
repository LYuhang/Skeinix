/**
 * Collapsible JSON tree renderer for `application/json`.
 *
 * Objects / arrays are expandable rows; the initial expand depth is capped
 * (~2 levels) so a large payload opens calm, not exploded. Primitive leaves
 * are typed and styled subtly (string / number / bool / null). A "Raw" toggle
 * swaps the tree for pretty-printed JSON with Copy.
 *
 * Large-tree guard: rendering is bounded by a global node budget
 * (`MAX_NODES`). When a subtree would exceed it, children are not rendered and
 * a "View full" affordance lazy-loads `output.path` instead (reuses
 * `ViewFullPanel`). Children of collapsed containers are not rendered at all
 * (lazy on expand), so the DOM stays small.
 *
 * Fail-soft: a body that does not parse as JSON falls back to `TextBlock`.
 */
import { useMemo, useState } from 'react';
import { ChevronDown, ChevronRight } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { cn } from '@/lib/utils';
import type { ToolEnvelopeOutput } from './parseEnvelope';
import { TextBlock } from './TextBlock';
import { CopyButton } from './CopyButton';
import { ViewFullPanel } from './ViewFullPanel';
import {
  countNodes,
  INITIAL_EXPAND_DEPTH,
  MAX_NODES,
  parseJson,
  type JsonValue,
} from './json-tree-utils';

export interface JsonTreeProps {
  output: ToolEnvelopeOutput;
  abstract: string;
  status: 'success' | 'error';
  wfId: string | undefined;
}

type Json = JsonValue;

function kindOf(v: Json): 'object' | 'array' | 'string' | 'number' | 'boolean' | 'null' {
  if (v === null) return 'null';
  if (Array.isArray(v)) return 'array';
  const t = typeof v;
  if (t === 'object') return 'object';
  if (t === 'number') return 'number';
  if (t === 'boolean') return 'boolean';
  return 'string';
}

function jsonPathKey(path: string[]): string {
  return JSON.stringify(path);
}

function collectRenderablePaths(value: Json, limit: number): Set<string> {
  const paths = new Set<string>();
  const stack: Array<{ value: Json; path: string[] }> = [{ value, path: [] }];
  while (stack.length > 0 && paths.size < limit) {
    const current = stack.pop();
    if (!current) break;
    paths.add(jsonPathKey(current.path));
    const entries = Array.isArray(current.value)
      ? current.value.map((child, index) => [String(index), child] as const)
      : current.value && typeof current.value === 'object'
        ? Object.entries(current.value as Record<string, Json>)
        : [];
    for (let index = entries.length - 1; index >= 0; index -= 1) {
      const [key, child] = entries[index];
      stack.push({ value: child, path: [...current.path, key] });
    }
  }
  return paths;
}

function Leaf({ value }: { value: Json }) {
  const kind = kindOf(value);
  const text =
    kind === 'string' ? `"${value as string}"` : kind === 'null' ? 'null' : String(value);
  const tone =
    kind === 'string'
      ? 'text-emerald-700 dark:text-emerald-400'
      : kind === 'number'
        ? 'text-sky-700 dark:text-sky-400'
        : kind === 'boolean'
          ? 'text-violet-700 dark:text-violet-400'
          : 'text-muted-foreground';
  return (
    <span className={cn('font-mono', tone)} data-json-kind={kind}>
      {text}
    </span>
  );
}

function Node({
  keyName,
  value,
  depth,
  path,
  renderablePaths,
}: {
  keyName?: string;
  value: Json;
  depth: number;
  path: string[];
  renderablePaths: Set<string>;
}) {
  const kind = kindOf(value);
  const isContainer = kind === 'object' || kind === 'array';
  const [open, setOpen] = useState(depth < INITIAL_EXPAND_DEPTH);

  const label = keyName !== undefined && (
    <span className="font-mono text-muted-foreground">{keyName}: </span>
  );

  if (!isContainer) {
    return (
      <div className="flex" style={{ paddingLeft: depth * 10 }}>
        {label}
        <Leaf value={value} />
      </div>
    );
  }

  const entries: [string, Json][] = Array.isArray(value)
    ? value.map((v, i) => [String(i), v])
    : Object.entries(value as Record<string, Json>);
  const Chevron = open ? ChevronDown : ChevronRight;
  const summary = Array.isArray(value)
    ? `[${entries.length}]`
    : `{${entries.length}}`;

  return (
    <div style={{ paddingLeft: depth * 10 }} data-json-kind={kind}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-0.5 text-left hover:underline focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
        aria-expanded={open}
      >
        <Chevron className="h-3 w-3 shrink-0 text-muted-foreground" />
        {label}
        <span className="font-mono text-muted-foreground">{summary}</span>
      </button>
      {open && entries.map(([k, v]) => {
        const childPath = [...path, k];
        if (!renderablePaths.has(jsonPathKey(childPath))) return null;
        return (
          <Node
            key={k}
            keyName={k}
            value={v}
            depth={depth + 1}
            path={childPath}
            renderablePaths={renderablePaths}
          />
        );
      })}
    </div>
  );
}

export function JsonTree({ output, abstract, status, wfId }: JsonTreeProps) {
  const { t } = useTranslation();
  const [raw, setRaw] = useState(false);

  const data = typeof output.data === 'string' ? output.data : undefined;
  const parsed = useMemo(() => parseJson(data), [data]);
  const pretty = useMemo(() => {
    if (!parsed.ok) return '';
    try {
      return JSON.stringify(parsed.value, null, 2);
    } catch {
      return data ?? '';
    }
  }, [parsed, data]);
  // Count total nodes to decide whether to offer "view full" (large guard).
  const nodeCount = useMemo(
    () => (parsed.ok ? countNodes(parsed.value) : 0),
    [parsed],
  );
  const renderablePaths = useMemo(
    () => (parsed.ok ? collectRenderablePaths(parsed.value, MAX_NODES) : new Set<string>()),
    [parsed],
  );

  // Fail-soft: not valid JSON (or large-omitted with no data) → TextBlock.
  // (All hooks above run unconditionally — this early return is hook-safe.)
  if (!parsed.ok) {
    return (
      <TextBlock output={output} abstract={abstract} status={status} wfId={wfId} />
    );
  }

  const tooLarge = nodeCount > MAX_NODES;
  return (
    <div className="space-y-1" data-role="json-tree">
      <div className="flex items-center justify-end gap-1">
        <button
          type="button"
          onClick={() => setRaw((v) => !v)}
          data-action="json-toggle-raw"
          className="rounded px-1.5 py-0.5 text-xs font-medium text-muted-foreground hover:bg-accent/60 hover:text-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
          aria-pressed={raw}
        >
          {raw ? t('tool.json_tree') : t('tool.json_raw')}
        </button>
        <CopyButton value={pretty} />
      </div>
      {raw ? (
        <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-words rounded border bg-muted/40 p-2 font-mono text-xs leading-snug">
          {pretty}
        </pre>
      ) : (
        <div className="max-h-72 overflow-auto rounded border bg-muted/30 p-2 text-xs leading-snug">
          <Node
            value={parsed.value}
            depth={0}
            path={[]}
            renderablePaths={renderablePaths}
          />
          {tooLarge && (
            <div
              className="mt-1 text-xs text-muted-foreground"
              data-role="json-truncated-notice"
            >
              {t('tool.json_truncated', { cap: MAX_NODES })}
            </div>
          )}
        </div>
      )}
      {output.path && (
        <ViewFullPanel
          wfId={wfId}
          path={output.path}
          render={(content) => (
            <pre className="max-h-96 overflow-auto whitespace-pre-wrap break-words rounded border bg-muted/40 p-2 font-mono text-xs leading-snug">
              {content}
            </pre>
          )}
        />
      )}
    </div>
  );
}
