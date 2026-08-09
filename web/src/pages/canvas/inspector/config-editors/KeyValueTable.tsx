/**
 * `KeyValueTable` — a validated key/value editor for flat string→string maps
 * (HTTPRequest `headers`). Per the Stream-3 amendment we render headers as a
 * key-value table rather than a raw JSON blob, so a non-technical user never
 * hand-writes JSON braces for the common case.
 *
 * Fully controlled: the parent owns the map. We keep the row LIST local
 * (including a blank trailing row + transient empty keys the user is typing)
 * and emit the committed map upward on every blur/remove. An empty key is
 * dropped from the emitted object (it isn't a valid header). When the map
 * is empty we emit `undefined` so the caller can delete the config key.
 */
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Label } from '@/components/ui/label';
import { Button } from '@/components/ui/button';
import { CommitOnBlurInput } from '@/pages/canvas/inspector/CommitOnBlur';

interface Row {
  key: string;
  value: string;
}

export interface KeyValueTableProps {
  label: string;
  value: unknown;
  readOnly?: boolean;
  onChange: (next: Record<string, string> | undefined) => void;
  'data-testid'?: string;
}

function toRows(value: unknown): Row[] {
  if (value && typeof value === 'object' && !Array.isArray(value)) {
    return Object.entries(value as Record<string, unknown>).map(([key, v]) => ({
      key,
      value: typeof v === 'string' ? v : String(v),
    }));
  }
  return [];
}

export function KeyValueTable({
  label,
  value,
  readOnly,
  onChange,
  'data-testid': testId,
}: KeyValueTableProps) {
  const { t } = useTranslation();
  const [rows, setRows] = useState<Row[]>(() => toRows(value));
  // Resync when upstream value changes (undo / agent / version-switch).
  const [prevValue, setPrevValue] = useState(value);
  if (prevValue !== value) {
    setPrevValue(value);
    setRows(toRows(value));
  }

  const commit = (next: Row[]) => {
    setRows(next);
    const obj: Record<string, string> = {};
    for (const r of next) {
      const k = r.key.trim();
      if (k) obj[k] = r.value;
    }
    onChange(Object.keys(obj).length === 0 ? undefined : obj);
  };

  const updateRow = (i: number, patch: Partial<Row>) => {
    commit(rows.map((r, idx) => (idx === i ? { ...r, ...patch } : r)));
  };

  const addRow = () => setRows([...rows, { key: '', value: '' }]);
  const removeRow = (i: number) => commit(rows.filter((_, idx) => idx !== i));

  return (
    <div className="space-y-1" data-testid={testId}>
      <Label className="text-xs">{label}</Label>
      {rows.length === 0 ? (
        <p className="text-xs text-muted-foreground">
          {t('inspector.config.kv.empty', 'No headers.')}
        </p>
      ) : (
        <ul className="space-y-1">
          {rows.map((r, i) => (
            <li key={i} className="grid grid-cols-[1fr_1fr_auto] items-center gap-1">
              <CommitOnBlurInput
                value={r.key}
                onCommit={(next) => updateRow(i, { key: next })}
                disabled={readOnly}
                placeholder={t('inspector.config.kv.keyPlaceholder', 'Header')}
                className="h-8 text-xs"
                data-testid={`${testId}-key-${i}`}
              />
              <CommitOnBlurInput
                value={r.value}
                onCommit={(next) => updateRow(i, { value: next })}
                disabled={readOnly}
                placeholder={t('inspector.config.kv.valuePlaceholder', 'value')}
                className="h-8 text-xs"
                data-testid={`${testId}-value-${i}`}
              />
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="h-8 px-2 text-xs"
                disabled={readOnly}
                onClick={() => removeRow(i)}
                data-testid={`${testId}-remove-${i}`}
                aria-label={`remove header ${i}`}
              >
                ×
              </Button>
            </li>
          ))}
        </ul>
      )}
      <Button
        type="button"
        variant="outline"
        size="sm"
        className="h-7 text-xs"
        disabled={readOnly}
        onClick={addRow}
        data-testid={`${testId}-add`}
      >
        {t('inspector.config.kv.addHeader', '+ Add header')}
      </Button>
    </div>
  );
}
