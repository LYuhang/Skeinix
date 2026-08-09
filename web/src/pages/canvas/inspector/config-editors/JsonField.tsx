/**
 * JSON textarea — used by the editors that surface a sub-tree (headers /
 * body / operations) as raw JSON. Local state holds the text the user is
 * typing; we only commit upstream when the buffer parses successfully on
 * blur. On a parse error we keep the buffer visible and flag the row with
 * an inline error message so the user can fix it without losing their
 * work.
 *
 * Why not a structured editor? The legacy inspector renders these as JSON
 * text for the same reason — a structured editor is too rich to ship in
 * the same task (TransformNode operations alone have 7 ops × variable
 * shapes). T15.5 / future tasks can swap in a structured editor without
 * touching the engine.
 */
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';

export interface JsonFieldProps {
  label: string;
  value: unknown;
  readOnly?: boolean;
  rows?: number;
  /** Commit a successfully-parsed value upstream. Not called on parse error. */
  onCommit: (next: unknown) => void;
  placeholder?: string;
}

function stringify(value: unknown): string {
  if (value === undefined) return '';
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

export function JsonField({
  label,
  value,
  readOnly,
  rows = 4,
  onCommit,
  placeholder,
}: JsonFieldProps) {
  const { t } = useTranslation();
  const [local, setLocal] = useState(() => stringify(value));
  const [error, setError] = useState<string | null>(null);
  // Prev-prop-in-state: resync local buffer + clear stale parse error when
  // the upstream `value` changes (undo, agent edit, etc.). React 19's
  // recommended replacement for `useEffect(() => setLocal(...), [value])`.
  const [prevValue, setPrevValue] = useState(value);
  if (prevValue !== value) {
    setPrevValue(value);
    setLocal(stringify(value));
    setError(null);
  }

  const commit = () => {
    const trimmed = local.trim();
    if (trimmed === '') {
      setError(null);
      onCommit(undefined);
      return;
    }
    try {
      const parsed = JSON.parse(trimmed) as unknown;
      setError(null);
      if (JSON.stringify(parsed) !== JSON.stringify(value)) {
        onCommit(parsed);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Invalid JSON');
    }
  };

  return (
    <div className="space-y-1">
      <Label className="text-xs">{label}</Label>
      <Textarea
        value={local}
        onChange={(e) => setLocal(e.target.value)}
        onBlur={commit}
        disabled={readOnly}
        rows={rows}
        placeholder={placeholder}
        className="font-mono text-xs"
      />
      {error && (
        <p className="text-xs text-destructive">
          {t('inspector.config.json.error', 'JSON error: {{msg}}', { msg: error })}
        </p>
      )}
    </div>
  );
}
