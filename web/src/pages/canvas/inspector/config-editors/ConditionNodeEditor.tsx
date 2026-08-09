/**
 * ConditionNode config editor — INTERACTIVE (Stream 3).
 *
 * Engine schema: `node_config.conditions` is an ordered list of
 * `{ condition_name, condition_str, next_node_id }`. The runtime evaluates
 * each `condition_str` (a Python expression with `{field}` placeholders
 * substituted from the node's own `input_fields`) in order; the first truthy
 * one wins, else the mandatory fallback row whose `condition_str.strip() ==
 * "others"` is taken (`condition.py`). The non-empty `next_node_id`s must
 * EXACTLY equal `children`.
 *
 * Ownership split (state-ownership rule): MEMBERSHIP (which rows have a
 * target + the children invariant) is owned by Stream 1's
 * connect/disconnect via `syncTypeConfigOnEdgeChange`. This editor owns the
 * EDITABLE attrs: `condition_name`, `condition_str` (via a structured
 * builder, raw behind "Advanced"), and re-targeting a row's `next_node_id`
 * among the node's EXISTING children (a dropdown of children minus those
 * already claimed by other rows). The mandatory "others" row is
 * auto-maintained, non-deletable, with `condition_str` fixed to "others".
 *
 * Inline warnings surface the issues the route Check misses today: an empty
 * `condition_str` on a real branch, conditions ≠ children (unmapped child,
 * or a row targeting a non-child), and a missing "others" fallback.
 */
import { useState } from 'react';
import { GripVertical } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Label } from '@/components/ui/label';
import { Button } from '@/components/ui/button';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { CommitOnBlurInput } from '@/pages/canvas/inspector/CommitOnBlur';
import type { NodeConfigEditorProps } from './types';
import { NodeRefSelect } from './NodeRefSelect';
import {
  childrenOf,
  inputFieldNames,
  startInputFieldNames,
  useDraftGraph,
  useSelectedNodeId,
} from './node-graph';
import {
  buildConditionStr,
  CONDITION_OPERATORS,
  type ConditionOperator,
} from './condition-builder';
import { isOthers, moveCondition, type ConditionEntry } from './condition-model';

/**
 * Reorder ONLY the non-others cards (the `others` fallback is always pinned
 * last), moving the non-others card at `from` to `to`, then re-append the
 * single `others` card. Pure — returns a NEW array; safe to unit-test.
 *
 * `from`/`to` index into the NON-OTHERS sublist (the order the UI renders the
 * draggable rows). Out-of-range / no-op moves return an equivalent array with
 * others still last.
 */
export interface ConditionNodeEditorProps extends NodeConfigEditorProps {
  /** Override the selected node id (tests). */
  nodeId?: string;
}

export function ConditionNodeEditor({
  config,
  readOnly,
  onChange,
  nodeId,
}: ConditionNodeEditorProps) {
  const { t } = useTranslation();
  const graph = useDraftGraph();
  const selectedId = useSelectedNodeId();
  const thisId = nodeId ?? selectedId;
  const [dragFrom, setDragFrom] = useState<number | null>(null);

  const conditions: ConditionEntry[] = Array.isArray(config.conditions)
    ? (config.conditions as ConditionEntry[])
    : [];

  const childRefs = childrenOf(graph, thisId);
  const childIds = childRefs.map((c) => c.id);

  // `{field}` placeholders come from the node's OWN input_fields; fall back
  // to the StartNode inputs when this node has declared none yet.
  const ownFields = inputFieldNames(graph, thisId);
  const fieldOptions = ownFields.length > 0 ? ownFields : startInputFieldNames(graph);

  const writeConditions = (next: ConditionEntry[]) =>
    onChange({ ...config, conditions: next });

  const updateRow = (index: number, patch: Partial<ConditionEntry>) => {
    const next = conditions.map((c, i) => (i === index ? { ...c, ...patch } : c));
    writeConditions(next);
  };

  const setAdvancedRow = (index: number, advanced: boolean) => {
    const next = conditions.map((c, i) => {
      if (i !== index) return c;
      if (!advanced) return { ...c, advanced: false };
      const { field: _field, operator: _operator, value: _value, ...rest } = c;
      void _field;
      void _operator;
      void _value;
      return { ...rest, advanced: true };
    });
    writeConditions(next);
  };

  const removeRow = (index: number) => {
    writeConditions(conditions.filter((_, i) => i !== index));
  };

  // Reorder via the pure helper, then commit through the same onChange path so
  // undo/dirty work. `from`/`to` index the NON-OTHERS sublist.
  const reorder = (from: number, to: number) => {
    writeConditions(moveCondition(conditions, from, to));
  };

  // Split for rendering: draggable real branches + the single pinned "others".
  const nonOthersRows = conditions
    .map((c, i) => ({ c, i }))
    .filter(({ c }) => !isOthers(c));
  const othersRow = conditions
    .map((c, i) => ({ c, i }))
    .find(({ c }) => isOthers(c));

  // --- warnings (route-Check-misses) ---------------------------------------
  const realRows = conditions.filter((c) => !isOthers(c));
  // The mapped set must include the "others" card's target — since it now
  // carries a real next_node_id (the default child), a child it points to is
  // NOT unmapped. Compute from ALL conditions.
  const mappedTargets = conditions
    .map((c) => c.next_node_id)
    .filter((t): t is string => typeof t === 'string' && t.length > 0);
  const claimedSet = new Set(mappedTargets);
  const unmappedChildren = childIds.filter((id) => !claimedSet.has(id));
  const targetsNotChild = mappedTargets.filter((t) => !childIds.includes(t));
  const emptyExprRows = realRows.filter(
    (c) =>
      (c.next_node_id ?? '') !== '' &&
      (c.condition_str ?? '').trim() === '',
  );
  const hasOthers = conditions.some(isOthers);

  return (
    <div className="space-y-2" data-testid="cfg-condition">
      <Label className="text-xs">conditions</Label>
      {conditions.length === 0 ? (
        <p className="text-xs text-muted-foreground">
          {t(
            'inspector.config.condition.empty',
            'No conditions yet — connect this node to branch targets on the canvas to create rows.',
          )}
        </p>
      ) : (
        <ul className="space-y-2">
          {/* Draggable real branches (reorderable; "others" pinned below). */}
          {nonOthersRows.map(({ c, i }, dragIndex) => {
            // A row's own target stays selectable; siblings' claims are hidden.
            const availableForRow = childRefs.filter(
              (ch) => ch.id === c.next_node_id || !claimedSet.has(ch.id),
            );
            return (
              <li
                key={i}
                draggable={!readOnly}
                onDragStart={() => setDragFrom(dragIndex)}
                onDragOver={(e) => {
                  if (readOnly || dragFrom === null) return;
                  e.preventDefault();
                }}
                onDrop={(e) => {
                  if (readOnly || dragFrom === null) return;
                  e.preventDefault();
                  reorder(dragFrom, dragIndex);
                  setDragFrom(null);
                }}
                onDragEnd={() => setDragFrom(null)}
                className="space-y-1.5 rounded border bg-muted/30 px-2 py-2"
                data-testid={`cfg-condition-row-${i}`}
              >
                <div className="flex items-center gap-1">
                  <span
                    aria-hidden
                    className={`shrink-0 text-muted-foreground ${
                      readOnly ? 'opacity-40' : 'cursor-grab active:cursor-grabbing'
                    }`}
                    data-testid={`cfg-condition-drag-${dragIndex}`}
                  >
                    <GripVertical className="h-3.5 w-3.5" />
                  </span>
                  <CommitOnBlurInput
                    value={c.condition_name ?? ''}
                    onCommit={(next) => updateRow(i, { condition_name: next })}
                    disabled={readOnly}
                    placeholder={t('inspector.config.condition.namePlaceholder', 'condition name')}
                    className="h-8 flex-1 text-xs"
                    data-testid={`cfg-condition-name-${i}`}
                  />
                </div>

                <ConditionExpr
                  value={c.condition_str ?? ''}
                  fieldOptions={fieldOptions}
                  readOnly={readOnly}
                  index={i}
                  initialAdvanced={c.advanced}
                  initialField={c.field}
                  initialOperator={c.operator}
                  initialValue={c.value}
                  onBuilderChange={(patch) => updateRow(i, patch)}
                  onAdvancedToggle={(adv) => setAdvancedRow(i, adv)}
                  onRawChange={(next) => updateRow(i, { condition_str: next })}
                />

                <NodeRefSelect
                  value={c.next_node_id}
                  options={availableForRow}
                  onChange={(next) => updateRow(i, { next_node_id: next })}
                  disabled={readOnly}
                  placeholder={t('inspector.config.condition.targetPlaceholder', 'target (a child node)')}
                  data-testid={`cfg-condition-target-${i}`}
                />

                <div className="flex justify-end">
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    className="h-6 px-2 text-xs"
                    disabled={readOnly}
                    onClick={() => removeRow(i)}
                    data-testid={`cfg-condition-remove-${i}`}
                  >
                    {t('inspector.config.condition.removeRow', 'Remove row')}
                  </Button>
                </div>
              </li>
            );
          })}

          {/* The single "others" fallback — always rendered LAST, not draggable. */}
          {othersRow && (
            <li
              key={othersRow.i}
              className="space-y-1.5 rounded border bg-muted/30 px-2 py-2"
              data-testid="cfg-condition-others"
            >
              <div className="text-xs font-medium text-muted-foreground">
                {t('inspector.config.condition.othersFallback', 'others (fallback)')}
              </div>
              <NodeRefSelect
                value={othersRow.c.next_node_id}
                options={childRefs.filter(
                  (ch) =>
                    ch.id === othersRow.c.next_node_id || !claimedSet.has(ch.id),
                )}
                onChange={(next) => updateRow(othersRow.i, { next_node_id: next })}
                disabled={readOnly}
                placeholder={t('inspector.config.condition.targetPlaceholder', 'target (a child node)')}
                data-testid="cfg-condition-others-target"
              />
            </li>
          )}
        </ul>
      )}

      {/* Inline warnings — the cheap local checks the route Check misses. */}
      <div className="space-y-0.5" data-testid="cfg-condition-warnings">
        {!hasOthers && (
          <Warn>
            {t(
              'inspector.config.condition.warnNoOthers',
              'Missing the mandatory "others" fallback row.',
            )}
          </Warn>
        )}
        {emptyExprRows.length > 0 && (
          <Warn>
            {t(
              'inspector.config.condition.warnEmptyExpr',
              '{{count}} branch row(s) have an empty condition — they will never match.',
              { count: emptyExprRows.length },
            )}
          </Warn>
        )}
        {unmappedChildren.length > 0 && (
          <Warn>
            {t(
              'inspector.config.condition.warnUnmapped',
              '{{count}} child edge(s) are not mapped to a condition row.',
              { count: unmappedChildren.length },
            )}
          </Warn>
        )}
        {targetsNotChild.length > 0 && (
          <Warn>
            {t(
              'inspector.config.condition.warnNotChild',
              '{{count}} row target(s) are not children of this node.',
              { count: targetsNotChild.length },
            )}
          </Warn>
        )}
      </div>

      <p className="text-xs text-muted-foreground">
        {t(
          'inspector.config.condition.hint',
          "Add/remove branch edges on the canvas; the row targets must match this node's children. {field} placeholders use this node's input fields.",
        )}
      </p>
    </div>
  );
}

function Warn({ children }: { children: React.ReactNode }) {
  return (
    <p className="flex items-start gap-1 text-xs text-state-warning">
      <span aria-hidden>⚠</span>
      <span>{children}</span>
    </p>
  );
}

// ---------------------------------------------------------------------------
// Structured condition builder (field / operator / value) with a raw escape.
// ---------------------------------------------------------------------------

interface ConditionExprProps {
  value: string;
  fieldOptions: string[];
  readOnly?: boolean;
  index: number;
  /** Persisted builder state on the card (restore-only). */
  initialAdvanced?: boolean;
  initialField?: string;
  initialOperator?: string;
  initialValue?: string;
  /** Commit the generated condition_str + the builder state together. */
  onBuilderChange: (patch: {
    condition_str: string;
    advanced: false;
    field: string;
    operator: string;
    value: string;
  }) => void;
  /** Persist only the advanced flag when toggling modes (keep condition_str). */
  onAdvancedToggle: (advanced: boolean) => void;
  /** Commit a raw (Advanced-mode) condition_str. */
  onRawChange: (next: string) => void;
}

function ConditionExpr({
  value,
  fieldOptions,
  readOnly,
  index,
  initialAdvanced,
  initialField,
  initialOperator,
  initialValue,
  onBuilderChange,
  onAdvancedToggle,
  onRawChange,
}: ConditionExprProps) {
  const { t } = useTranslation();
  // Initialize FROM the card's persisted builder state when present, else the
  // current defaults. Reset across selected-node changes by the editor's key
  // remount in NodeTab.
  const [advanced, setAdvanced] = useState(
    initialAdvanced ?? value.trim().length > 0,
  );
  const [field, setField] = useState(initialField ?? fieldOptions[0] ?? '');
  const [operator, setOperator] = useState<ConditionOperator>(
    (initialOperator as ConditionOperator) ?? '==',
  );
  const [val, setVal] = useState(initialValue ?? '');

  const generate = (f: string, op: ConditionOperator, v: string) => {
    onBuilderChange({
      condition_str: buildConditionStr(f, op, v),
      advanced: false,
      field: f,
      operator: op,
      value: v,
    });
  };

  const toggleAdvanced = (adv: boolean) => {
    setAdvanced(adv);
    if (adv) {
      onAdvancedToggle(true);
    } else {
      generate(field, operator, val);
    }
  };

  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between">
        <Label className="text-xs text-muted-foreground">
          {advanced
            ? t('inspector.config.condition.rawLabel', 'Raw expression')
            : t('inspector.config.condition.label', 'Condition')}
        </Label>
        <label className="flex items-center gap-1 text-xs text-muted-foreground">
          <input
            type="checkbox"
            checked={advanced}
            disabled={readOnly}
            onChange={(e) => toggleAdvanced(e.target.checked)}
            data-testid={`cfg-condition-advanced-${index}`}
          />
          {t('inspector.config.condition.advanced', 'Advanced')}
        </label>
      </div>

      {advanced ? (
        <CommitOnBlurInput
          value={value}
          onCommit={onRawChange}
          disabled={readOnly}
          placeholder="{score} >= 0.8 or {flag} == True"
          className="h-8 font-mono text-xs"
          data-testid={`cfg-condition-raw-${index}`}
        />
      ) : (
        <div className="grid grid-cols-[1fr_auto_1fr] gap-1">
          <Select
            value={field || undefined}
            onValueChange={(f) => {
              setField(f);
              generate(f, operator, val);
            }}
            disabled={readOnly || fieldOptions.length === 0}
          >
            <SelectTrigger
              className="h-8 text-xs"
              data-testid={`cfg-condition-field-${index}`}
            >
              <SelectValue placeholder={t('inspector.config.condition.fieldPlaceholder', 'field')} />
            </SelectTrigger>
            <SelectContent>
              {fieldOptions.map((f) => (
                <SelectItem key={f} value={f} className="text-xs">
                  {f}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>

          <Select
            value={operator}
            onValueChange={(op) => {
              const next = op as ConditionOperator;
              setOperator(next);
              generate(field, next, val);
            }}
            disabled={readOnly}
          >
            <SelectTrigger
              className="h-8 w-[72px] text-xs"
              data-testid={`cfg-condition-op-${index}`}
            >
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {CONDITION_OPERATORS.map((op) => (
                <SelectItem key={op} value={op} className="text-xs">
                  {op}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>

          <CommitOnBlurInput
            value={val}
            onCommit={(v) => {
              setVal(v);
              generate(field, operator, v);
            }}
            disabled={readOnly}
            placeholder={t('inspector.config.condition.valuePlaceholder', 'value')}
            className="h-8 text-xs"
            data-testid={`cfg-condition-value-${index}`}
          />
        </div>
      )}

      {value && (
        <p
          className="font-mono text-xs text-muted-foreground"
          data-testid={`cfg-condition-preview-${index}`}
        >
          → {value}
        </p>
      )}
    </div>
  );
}
