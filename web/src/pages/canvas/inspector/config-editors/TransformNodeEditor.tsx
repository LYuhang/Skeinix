/**
 * TransformNode config editor — INTERACTIVE, OUTPUT-FIELD-DRIVEN (N→M).
 *
 * Engine schema (`engine/.../nodes/transform.py`): `node_config` is NO LONGER
 * `{operations: [...]}`. It is now an N→M per-output mapping:
 *
 *   { "mappings": [
 *     { "input_field":  "<one of the node's INPUT field names>",
 *       "output_field": "<one of the node's OUTPUT field names>",
 *       "transform_list": [ {"op": "...", ...op-params }, ... ] } ] }
 *
 * Runtime semantics (what the param editors below mirror EXACTLY): for each
 * mapping the chain starts from `inputs[input_field]` and each op transforms the
 * RUNNING VALUE, then the final value is assigned to `output_field`. The 7
 * value-level ops and their params:
 *
 *   path     { op:'path', path:'data.user.name' }   // dot-bracket nav; '' = identity
 *   index    { op:'index', index:0 }                // Nth list elem, 0-based (-1 = last)
 *   length   { op:'length' }                        // len(); NO params
 *   cast     { op:'cast', to:'string'|'number'|'integer'|'boolean' }
 *   default  { op:'default', value:<any> }          // fallback when value is None
 *   compute  { op:'compute', expr:'{value} * 2' }   // {value} placeholder
 *   pick     { op:'pick', fields:['a','b'] }         // keep dict sub-keys
 *
 * UI: one MAPPING BLOCK per declared OUTPUT field. The header binds the block's
 * `input_field`; the body is the ordered, reorderable value-transform chain
 * (native HTML5 DnD via the pure `moveTransform` helper, same pattern as the old
 * `operations` editor). Blocks are DERIVED for display: a block with no stored
 * mapping renders a default (`input_field:''`, empty chain) but is NOT written to
 * `node_config.mappings` until the user edits it (`upsertMapping`). Switching a
 * row's op type resets it to that op's fresh default (`defaultTransform`).
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
import {
  defaultTransform,
  moveTransform,
  TRANSFORM_OP_TYPES,
  type TransformMapping,
  type TransformOp,
  type TransformOpType,
} from './transform-model';

// ---------------------------------------------------------------------------
// Model
// ---------------------------------------------------------------------------

type Mapping = TransformMapping;

const CAST_TARGET_TYPES = ['string', 'number', 'integer', 'boolean'] as const;

export type TransformNodeEditorProps = NodeConfigEditorProps;

export function TransformNodeEditor({
  config,
  readOnly,
  onChange,
  inputFieldNames = [],
  outputFieldNames = [],
}: TransformNodeEditorProps) {
  const { t } = useTranslation();

  const mappings: Mapping[] = Array.isArray(config.mappings)
    ? (config.mappings as Mapping[])
    : [];

  /** Derived (display-only) mapping for an output field; not yet persisted. */
  const mappingFor = (name: string): Mapping =>
    mappings.find((m) => m.output_field === name) ?? {
      input_field: '',
      output_field: name,
      transform_list: [],
    };

  /**
   * Replace (or append) the mapping for `next.output_field`, then emit a new
   * `node_config`. Every per-block edit funnels through here, so editing a
   * derived block materializes its mapping on first edit.
   */
  const upsertMapping = (next: Mapping) => {
    const exists = mappings.some((m) => m.output_field === next.output_field);
    const nextMappings = exists
      ? mappings.map((m) => (m.output_field === next.output_field ? next : m))
      : [...mappings, next];
    onChange({ ...config, mappings: nextMappings });
  };

  if (outputFieldNames.length === 0) {
    return (
      <div className="space-y-2" data-testid="cfg-transform">
        <p
          className="text-xs text-muted-foreground"
          data-testid="transform-no-outputs"
        >
          {t(
            'inspector.config.transform.noOutputs',
            'No output fields yet — add an output field above to configure its transform.',
          )}
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-3" data-testid="cfg-transform">
      {outputFieldNames.map((name) => (
        <MappingBlock
          key={name}
          name={name}
          mapping={mappingFor(name)}
          inputFieldNames={inputFieldNames}
          readOnly={readOnly}
          onChange={upsertMapping}
        />
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Per-output mapping block
// ---------------------------------------------------------------------------

interface MappingBlockProps {
  name: string;
  mapping: Mapping;
  inputFieldNames: string[];
  readOnly?: boolean;
  onChange: (next: Mapping) => void;
}

function MappingBlock({
  name,
  mapping,
  inputFieldNames,
  readOnly,
  onChange,
}: MappingBlockProps) {
  const { t } = useTranslation();
  const [dragFrom, setDragFrom] = useState<number | null>(null);

  const list = Array.isArray(mapping.transform_list)
    ? mapping.transform_list
    : [];

  const setInput = (input_field: string) =>
    onChange({ ...mapping, input_field });

  const writeList = (transform_list: TransformOp[]) =>
    onChange({ ...mapping, transform_list });

  const updateRow = (index: number, next: TransformOp) =>
    writeList(list.map((o, i) => (i === index ? next : o)));

  const changeType = (index: number, type: TransformOpType) => {
    if (list[index]?.op === type) return;
    updateRow(index, defaultTransform(type));
  };

  const removeRow = (index: number) =>
    writeList(list.filter((_, i) => i !== index));

  const addRow = () => writeList([...list, defaultTransform('path')]);

  const reorder = (from: number, to: number) =>
    writeList(moveTransform(list, from, to));

  const opLabel = (type: string) =>
    t(`inspector.config.transform.op.${type}`, type);

  return (
    <div
      className="space-y-2 rounded border bg-muted/20 px-2 py-2"
      data-testid={`transform-block-${name}`}
    >
      {/* Header: output label + input select */}
      <div className="flex items-center gap-2">
        <Label className="shrink-0 text-xs font-medium">→ {name}</Label>
        <span className="text-xs text-muted-foreground">
          {t('inspector.config.transform.dependsOn', 'From input')}
        </span>
        <Select
          value={mapping.input_field || undefined}
          onValueChange={(v) => setInput(v)}
          disabled={readOnly}
        >
          <SelectTrigger
            className="h-8 flex-1 text-xs"
            data-testid={`transform-input-${name}`}
          >
            <SelectValue
              placeholder={t(
                'inspector.config.transform.selectInput',
                'Select an input field',
              )}
            />
          </SelectTrigger>
          <SelectContent>
            {inputFieldNames.map((f) => (
              <SelectItem key={f} value={f} className="text-xs">
                {f}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {/* Transform chain */}
      {list.length === 0 ? (
        <p className="text-xs text-muted-foreground">
          {t(
            'inspector.config.transform.passthrough',
            'Passes the input value through unchanged.',
          )}
        </p>
      ) : (
        <ul className="space-y-1.5">
          {list.map((op, i) => (
            <li
              key={i}
              draggable={!readOnly}
              onDragStart={() => setDragFrom(i)}
              onDragOver={(e) => {
                if (readOnly || dragFrom === null) return;
                e.preventDefault();
              }}
              onDrop={(e) => {
                if (readOnly || dragFrom === null) return;
                e.preventDefault();
                reorder(dragFrom, i);
                setDragFrom(null);
              }}
              onDragEnd={() => setDragFrom(null)}
              className="space-y-1.5 rounded border bg-background px-2 py-1.5"
              data-testid={`transform-row-${name}-${i}`}
            >
              <div className="flex items-center gap-1">
                <span
                  aria-hidden
                  className={`shrink-0 text-muted-foreground ${
                    readOnly ? 'opacity-40' : 'cursor-grab active:cursor-grabbing'
                  }`}
                >
                  <GripVertical className="h-3.5 w-3.5" />
                </span>
                <Select
                  value={typeof op.op === 'string' ? op.op : undefined}
                  onValueChange={(v) => changeType(i, v as TransformOpType)}
                  disabled={readOnly}
                >
                  <SelectTrigger
                    className="h-8 flex-1 text-xs"
                    data-testid={`transform-op-${name}-${i}`}
                  >
                    <SelectValue
                      placeholder={t(
                        'inspector.config.transform.typePlaceholder',
                        'operation type',
                      )}
                    />
                  </SelectTrigger>
                  <SelectContent>
                    {TRANSFORM_OP_TYPES.map((type) => (
                      <SelectItem key={type} value={type} className="text-xs">
                        {opLabel(type)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  className="h-6 px-2 text-xs"
                  disabled={readOnly}
                  onClick={() => removeRow(i)}
                  aria-label={t(
                    'inspector.config.transform.removeOp',
                    'Remove operation',
                  )}
                  data-testid={`transform-remove-${name}-${i}`}
                >
                  ✕
                </Button>
              </div>

              <TransformParams
                name={name}
                index={i}
                op={op}
                readOnly={readOnly}
                onChange={(next) => updateRow(i, next)}
              />
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
        data-testid={`transform-add-${name}`}
      >
        {t('inspector.config.transform.addTransform', 'Add transform')}
      </Button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Per-op param inputs
// ---------------------------------------------------------------------------

interface TransformParamsProps {
  name: string;
  index: number;
  op: TransformOp;
  readOnly?: boolean;
  onChange: (next: TransformOp) => void;
}

function TransformParams({
  name,
  index,
  op,
  readOnly,
  onChange,
}: TransformParamsProps) {
  const { t } = useTranslation();
  const set = (patch: TransformOp) => onChange({ ...op, ...patch });

  switch (op.op) {
    case 'path':
      return (
        <Field label={t('inspector.config.transform.pathLabel', 'path (dot-bracket)')}>
          <CommitOnBlurInput
            value={typeof op.path === 'string' ? (op.path as string) : ''}
            onCommit={(v) => set({ path: v })}
            disabled={readOnly}
            placeholder="data.user.name"
            className="h-8 text-xs"
            data-testid={`transform-param-${name}-${index}-path`}
          />
        </Field>
      );

    case 'index':
      return (
        <Field label={t('inspector.config.transform.indexLabel', 'index (0-based, -1 = last)')}>
          <CommitOnBlurInput
            value={op.index != null ? String(op.index) : '0'}
            onCommit={(v) => {
              const n = parseInt(v, 10);
              set({ index: Number.isFinite(n) ? n : 0 });
            }}
            disabled={readOnly}
            placeholder="0"
            className="h-8 text-xs"
            data-testid={`transform-param-${name}-${index}-index`}
          />
        </Field>
      );

    case 'length':
      return null;

    case 'cast':
      return (
        <Field label={t('inspector.config.transform.toLabel', 'to type')}>
          <Select
            value={typeof op.to === 'string' ? (op.to as string) : 'string'}
            onValueChange={(v) => set({ to: v })}
            disabled={readOnly}
          >
            <SelectTrigger
              className="h-8 text-xs"
              data-testid={`transform-param-${name}-${index}-to`}
            >
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {CAST_TARGET_TYPES.map((tp) => (
                <SelectItem key={tp} value={tp} className="text-xs">
                  {tp}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </Field>
      );

    case 'default':
      return (
        <Field label={t('inspector.config.transform.valueLabel', 'value (JSON)')}>
          <CommitOnBlurInput
            value={jsonString(op.value)}
            onCommit={(v) => set({ value: parseJsonLoose(v) })}
            disabled={readOnly}
            placeholder='0 or "n/a" or [1,2]'
            className="h-8 font-mono text-xs"
            data-testid={`transform-param-${name}-${index}-value`}
          />
        </Field>
      );

    case 'compute':
      return (
        <Field label={t('inspector.config.transform.exprLabel', 'expression')}>
          <CommitOnBlurInput
            value={
              typeof op.expr === 'string'
                ? (op.expr as string)
                : typeof op.expression === 'string'
                  ? (op.expression as string)
                  : ''
            }
            onCommit={(v) => {
              // Editing an old workflow upgrades it in place to the canonical
              // key so the legacy alias cannot linger in the next commit.
              const current = { ...op };
              delete current.expression;
              onChange({ ...current, expr: v });
            }}
            disabled={readOnly}
            placeholder="{value} * 2"
            className="h-8 font-mono text-xs"
            data-testid={`transform-param-${name}-${index}-expr`}
          />
        </Field>
      );

    case 'pick':
      return (
        <Field
          label={t(
            'inspector.config.transform.fieldsLabel',
            'fields (comma-separated)',
          )}
        >
          <CommitOnBlurInput
            value={
              Array.isArray(op.fields)
                ? (op.fields as unknown[]).map(String).join(', ')
                : ''
            }
            onCommit={(v) =>
              set({
                fields: v
                  .split(',')
                  .map((s) => s.trim())
                  .filter((s) => s.length > 0),
              })
            }
            disabled={readOnly}
            placeholder="a, b, c"
            className="h-8 text-xs"
            data-testid={`transform-param-${name}-${index}-fields`}
          />
        </Field>
      );

    default:
      return null;
  }
}

// ---------------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------------

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-1">
      <Label className="text-xs text-muted-foreground">{label}</Label>
      {children}
    </div>
  );
}

function jsonString(value: unknown): string {
  if (value === undefined) return '';
  if (typeof value === 'string') return value;
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

/** Parse JSON; on failure keep the raw string (so plain text "n/a" works). */
function parseJsonLoose(raw: string): unknown {
  const trimmed = raw.trim();
  if (trimmed === '') return '';
  try {
    return JSON.parse(trimmed);
  } catch {
    return raw;
  }
}
