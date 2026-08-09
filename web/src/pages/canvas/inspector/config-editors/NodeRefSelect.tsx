/**
 * `NodeRefSelect` — a dropdown for any field that names another node
 * (next_node_id, pairing pointers). Labels options by `node_name`; never
 * free-text a node id (Stream 3 directive).
 *
 * A nullable selection is supported via a `__none__` sentinel (Radix
 * Select forbids an empty-string item value): picking it emits `null`.
 */
import { useTranslation } from 'react-i18next';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import type { NodeRef } from './node-graph';

const NONE = '__none__';

export interface NodeRefSelectProps {
  /** Current node id, or null/empty when unset. */
  value: string | null | undefined;
  options: NodeRef[];
  /** Emits the chosen node id, or null when the "none" option is picked. */
  onChange: (next: string | null) => void;
  disabled?: boolean;
  placeholder?: string;
  /** Render a leading "— (none) —" option (default true). */
  allowNone?: boolean;
  'data-testid'?: string;
}

export function NodeRefSelect({
  value,
  options,
  onChange,
  disabled,
  placeholder,
  allowNone = true,
  'data-testid': testId,
}: NodeRefSelectProps) {
  const { t } = useTranslation();
  const resolvedPlaceholder =
    placeholder ?? t('inspector.config.noderef.selectNode', 'Select a node');
  const current = value && value.length > 0 ? value : NONE;
  // If the current value references a node no longer in `options` (e.g. a
  // claimed/deleted target), still show it so the user sees the stale ref.
  const knownIds = new Set(options.map((o) => o.id));
  const showOrphan = value && value.length > 0 && !knownIds.has(value);

  return (
    <Select
      value={current}
      onValueChange={(next) => onChange(next === NONE ? null : next)}
      disabled={disabled}
    >
      <SelectTrigger className="h-8 text-xs" data-testid={testId}>
        <SelectValue placeholder={resolvedPlaceholder} />
      </SelectTrigger>
      <SelectContent>
        {allowNone && (
          <SelectItem value={NONE} className="text-xs text-muted-foreground">
            {t('inspector.config.noderef.none', '— (none) —')}
          </SelectItem>
        )}
        {showOrphan && (
          <SelectItem value={value as string} className="text-xs">
            {t('inspector.config.noderef.missing', '{{value}} (missing)', {
              value,
            })}
          </SelectItem>
        )}
        {options.map((o) => (
          <SelectItem key={o.id} value={o.id} className="text-xs">
            {o.name}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
