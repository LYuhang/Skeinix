import { JsonTree } from './JsonTree';
import { useTranslation } from 'react-i18next';

function parseArguments(value: string): unknown {
  if (!value.trim()) return null;
  try {
    return JSON.parse(value);
  } catch {
    return value;
  }
}

function objectValue(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

const SENSITIVE_KEY = /(?:password|passwd|token|cookie|authorization|secret|credential|api[_-]?key)/i;

function redact(value: unknown, key = '', depth = 0): unknown {
  if (SENSITIVE_KEY.test(key)) return '[redacted]';
  if (depth > 12) return '[nested value omitted]';
  if (Array.isArray(value)) return value.slice(0, 200).map((item) => redact(item, key, depth + 1));
  const object = objectValue(value);
  if (!object) return value;
  return Object.fromEntries(
    Object.entries(object).slice(0, 200).map(([childKey, child]) => [
      childKey,
      redact(child, childKey, depth + 1),
    ]),
  );
}

export function ToolArgumentsView({
  toolName,
  argumentsText,
}: {
  toolName: string;
  argumentsText: string;
}) {
  const { t } = useTranslation();
  const parsed = parseArguments(argumentsText);
  const safeParsed = redact(parsed);
  const object = objectValue(safeParsed);
  if (toolName === 'bash' || toolName === 'run_command' || toolName === 'shell') {
    const command = typeof object?.command === 'string' ? object.command : argumentsText;
    const timeout = typeof object?.timeout_s === 'number' ? object.timeout_s : null;
    return (
      <div className="overflow-hidden rounded-md border border-zinc-700 bg-zinc-950 font-mono text-xs text-zinc-100">
        <div className="flex gap-2 border-b border-zinc-800 px-3 py-2">
          <span className="select-none text-emerald-400">$</span>
          <pre className="min-w-0 whitespace-pre-wrap break-words">{command || '(empty command)'}</pre>
        </div>
        {timeout !== null && (
          <div className="px-3 py-1 text-xs text-zinc-500">timeout {timeout}s</div>
        )}
      </div>
    );
  }
  if (toolName === 'edit_file' && object) {
    const path = typeof object.path === 'string' ? object.path : '';
    const replaceAll = object.replace_all === true;
    return (
      <div className="rounded-md border border-edge-subtle bg-surface-sunken px-3 py-2 text-xs">
        <div className="truncate font-mono font-medium text-foreground">{path || 'File edit'}</div>
        <div className="mt-1 text-muted-foreground">
          {replaceAll ? 'Replace every exact match' : 'Replace one exact match'}
        </div>
        <details className="mt-2">
          <summary className="cursor-pointer text-muted-foreground hover:text-foreground">
            {t('tool.arguments.exactText', 'Exact text details')}
          </summary>
          <div className="mt-2 grid gap-2 lg:grid-cols-2">
            <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-words rounded bg-state-danger/10 p-2 font-mono text-xs">
              {typeof object.old_string === 'string' ? object.old_string : ''}
            </pre>
            <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-words rounded bg-state-success/10 p-2 font-mono text-xs">
              {typeof object.new_string === 'string' ? object.new_string : ''}
            </pre>
          </div>
        </details>
      </div>
    );
  }
  if (safeParsed !== null && typeof safeParsed === 'object') {
    return (
      <JsonTree
        output={{ content_type: 'application/json', data: JSON.stringify(safeParsed) }}
        abstract="Tool parameters"
        status="success"
        wfId={undefined}
      />
    );
  }
  return (
    <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-words rounded-md border border-edge-subtle bg-surface-sunken p-2 font-mono text-xs leading-snug">
      {typeof safeParsed === 'string' ? safeParsed : '(none)'}
    </pre>
  );
}
