/**
 * Card-style input/output field-dict editor (Stream 5).
 *
 * The legacy `BaseNode.GENERAL_NODE_SCHEMA` keys both `input_fields` and
 * `output_fields` as `Record<name, FieldEntry>` where:
 *   - `input_fields[name]`  = `{ type, value?, reference? }`
 *   - `output_fields[name]` = `{ type, description? }`
 *
 * This replaces the old 5-column grid with a vertical list of CARDS, each
 * field on its own card — easier to scan + edit for non-technical users:
 *
 *   INPUT card:  [⠿ drag] [type ▾]  [name…………]  [×]
 *                value: <FieldValueWidget> (reference dropdown OR preset)
 *   OUTPUT card: [⠿ drag] [type ▾]  [name…………]  [×]
 *                description: <text>
 *
 * Reorder
 * -------
 * Cards are reorderable by HTML5 drag (the same primitive the template
 * palette uses — no extra dep). The persisted order is the OBJECT KEY
 * insertion order of `input_fields`/`output_fields`; JS objects + `JSON`
 * preserve string-key order and the engine reads them via `Object.entries`,
 * so reordering = rebuilding the dict in the new key order (`reorderFields`).
 *
 * Output-follows-input (Stream 5, user msg3)
 * ------------------------------------------
 * When `outputsFollowInputs` is set (StartNode etc.), the OUTPUT section is a
 * READ-ONLY VIEW of the inputs: it mirrors name + type, drops value/reference,
 * hides add/×/drag, and shows the caption "Outputs mirror your inputs". The
 * mirror is materialized into the draft at edit time by the caller (see
 * `NodeTab` + `materializeMirroredOutputs`); this component just renders it.
 *
 * State-ownership (Stream 0b)
 * ---------------------------
 * This component holds NO authoritative state. Ref/Preset mode is DERIVED
 * from `entry.reference` truthiness (inside `FieldValueWidget`). Rename
 * rejection messages are the only local state, and the whole editor subtree
 * is remounted via `key={selected.id}` from `NodeTab`, so nothing leaks
 * across a selected-node change (undo / agent / version-switch).
 *
 * Type list comes from `useEnums().field_types`; falls back to a static
 * six-item list so the dropdown is never empty on first paint.
 */
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ArrowDownToLine, ArrowUpFromLine, GripVertical, Plus, X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { CommitOnBlurInput } from '@/pages/canvas/inspector/CommitOnBlur';
import {
  FieldValueWidget,
  type FieldSlotValue,
} from '@/pages/canvas/inspector/FieldValueWidget';
import { getEnumList, useEnums } from '@/lib/api/queries/enums';
import { reorderFields } from './reorder-fields';
import { InspectorSection } from './InspectorSection';

export interface FieldEntry {
  type: string;
  value?: unknown;
  reference?: string;
  description?: string;
}

export type FieldsMap = Record<string, FieldEntry>;

export interface FieldsEditorProps {
  title: string;
  fields: FieldsMap;
  mode: 'input' | 'output';
  readOnly?: boolean;
  /** Reference dropdown options (`producer_node_name.output_field`). */
  referenceCandidates?: string[];
  /**
   * Output-only: render as a read-only mirror of the inputs (StartNode etc.).
   * Add/×/drag controls are hidden and the cards are non-editable.
   */
  outputsFollowInputs?: boolean;
  onChange: (next: FieldsMap) => void;
}

const FALLBACK_FIELD_TYPES = [
  'string',
  'number',
  'integer',
  'boolean',
  'array',
  'object',
];

/**
 * Rebuild a fields dict moving the key at `from` to `to` (insertion order is
 * the persisted order). Pure + exported for tests. Out-of-range indices or a
 * no-op move return the original reference.
 */
export function FieldsEditor({
  title,
  fields,
  mode,
  readOnly = false,
  referenceCandidates = [],
  outputsFollowInputs = false,
  onChange,
}: FieldsEditorProps) {
  const { t } = useTranslation();
  const { data: enums } = useEnums();
  const fromEnum = getEnumList(enums, 'field_types');
  const fieldTypes = fromEnum.length > 0 ? fromEnum : FALLBACK_FIELD_TYPES;

  const names = Object.keys(fields);
  const mirrored = mode === 'output' && outputsFollowInputs;

  // Per-row rename rejection message (the only local state; reset on remount).
  const [errorByName, setErrorByName] = useState<Record<string, string>>({});
  // Drag source index for HTML5 reorder.
  const [dragFrom, setDragFrom] = useState<number | null>(null);

  const setError = (name: string, message: string | null) =>
    setErrorByName((m) => {
      const rest = { ...m };
      if (message) rest[name] = message;
      else delete rest[name];
      return rest;
    });

  const commitName = (oldName: string, newName: string) => {
    const trimmed = newName.trim();
    if (trimmed === oldName) {
      setError(oldName, null);
      return;
    }
    if (!trimmed) {
      setError(oldName, t('inspector.fields.name_empty', 'Name cannot be empty.'));
      return;
    }
    if (Object.prototype.hasOwnProperty.call(fields, trimmed)) {
      setError(
        oldName,
        t('inspector.fields.name_used', '"{{name}}" is already used.', {
          name: trimmed,
        }),
      );
      return;
    }
    const next: FieldsMap = {};
    for (const k of names) next[k === oldName ? trimmed : k] = fields[k];
    setError(oldName, null);
    onChange(next);
  };

  const setEntry = (name: string, entry: FieldEntry) =>
    onChange({ ...fields, [name]: entry });

  const setSlot = (name: string, slot: FieldSlotValue) =>
    setEntry(name, {
      ...fields[name],
      value: slot.value,
      reference: slot.reference ?? '',
    });

  const removeEntry = (name: string) => {
    const next: FieldsMap = {};
    for (const k of names) if (k !== name) next[k] = fields[k];
    setError(name, null);
    onChange(next);
  };

  const addEntry = () => {
    let n = names.length + 1;
    while (Object.prototype.hasOwnProperty.call(fields, `field_${n}`)) n += 1;
    const name = `field_${n}`;
    const entry: FieldEntry =
      mode === 'input'
        ? { type: 'string', value: '', reference: '' }
        : { type: 'string', description: '' };
    onChange({ ...fields, [name]: entry });
  };

  const onDrop = (toIndex: number) => {
    if (dragFrom === null) return;
    const next = reorderFields(fields, dragFrom, toIndex);
    setDragFrom(null);
    if (next !== fields) onChange(next);
  };

  const TypeSelect = ({ name }: { name: string }) => (
    <select
      aria-label={`field type ${name}`}
      data-testid={`field-type-${name}`}
      className="h-8 w-[110px] rounded-md border border-input bg-background px-2 text-xs"
      value={fields[name].type}
      disabled={readOnly}
      onChange={(e) => setEntry(name, { ...fields[name], type: e.target.value })}
    >
      {fieldTypes.map((t) => (
        <option key={t} value={t}>
          {t}
        </option>
      ))}
    </select>
  );

  const renderHeader = (name: string, index: number) => (
    <div className="flex items-center gap-1.5">
      <span
        data-testid={`field-drag-${name}`}
        aria-label={`reorder field ${name}`}
        draggable={!readOnly}
        onDragStart={() => setDragFrom(index)}
        onDragEnd={() => setDragFrom(null)}
        className={
          readOnly
            ? 'text-muted-foreground/40'
            : 'cursor-grab text-muted-foreground active:cursor-grabbing'
        }
      >
        <GripVertical className="h-4 w-4" />
      </span>
      <TypeSelect name={name} />
      {/* readOnly (not disabled) so a fixed/locked field NAME stays
          selectable + keyboard-copyable — a disabled input's text can't be
          selected. The muted class keeps the "locked" greyed look. */}
      <CommitOnBlurInput
        aria-label={`field name ${name}`}
        data-testid={`field-name-${name}`}
        value={name}
        onCommit={(next) => commitName(name, next)}
        readOnly={readOnly}
        className={`h-8 flex-1 text-xs${
          readOnly ? ' bg-muted/40 text-muted-foreground' : ''
        }`}
      />
      <Button
        type="button"
        variant="ghost"
        size="icon"
        className="h-8 w-8 shrink-0 text-state-danger hover:text-state-danger"
        onClick={() => removeEntry(name)}
        disabled={readOnly}
        aria-label={`remove field ${name}`}
      >
        <X className="h-3.5 w-3.5" />
      </Button>
    </div>
  );

  const cardClasses =
    'space-y-2 border-b border-edge-subtle py-2 last:border-b-0';

  const renderInputCard = (name: string, index: number) => {
    const entry = fields[name];
    return (
      <div
        key={name}
        data-testid={`field-card-${name}`}
        className={cardClasses}
        onDragOver={(e) => {
          if (dragFrom !== null) e.preventDefault();
        }}
        onDrop={() => onDrop(index)}
      >
        {renderHeader(name, index)}
        <FieldValueWidget
          type={entry.type}
          value={entry.value}
          reference={entry.reference ?? ''}
          referenceCandidates={referenceCandidates}
          readOnly={readOnly}
          idBase={`field-${name}`}
          onChange={(slot) => setSlot(name, slot)}
        />
        {errorByName[name] && (
          <p className="text-xs text-destructive">{errorByName[name]}</p>
        )}
      </div>
    );
  };

  const renderOutputCard = (name: string, index: number) => {
    const entry = fields[name];
    return (
      <div
        key={name}
        data-testid={`field-card-${name}`}
        className={cardClasses}
        onDragOver={(e) => {
          if (dragFrom !== null) e.preventDefault();
        }}
        onDrop={() => onDrop(index)}
      >
        {renderHeader(name, index)}
        {/* readOnly (not disabled) → the locked DESCRIPTION text stays
            selectable + copyable. */}
        <CommitOnBlurInput
          aria-label={`field description ${name}`}
          data-testid={`field-description-${name}`}
          value={entry.description ?? ''}
          onCommit={(next) => setEntry(name, { ...entry, description: next })}
          placeholder={t('inspector.fields.desc_placeholder', 'description')}
          readOnly={readOnly}
          className={`h-8 text-xs${
            readOnly ? ' bg-muted/40 text-muted-foreground' : ''
          }`}
        />
        {errorByName[name] && (
          <p className="text-xs text-destructive">{errorByName[name]}</p>
        )}
      </div>
    );
  };

  // Read-only mirror: name + type only, no controls.
  const renderMirrorCard = (name: string) => {
    const entry = fields[name];
    return (
      <div
        key={name}
        data-testid={`field-card-${name}`}
        className="flex items-center gap-2 border-b border-edge-subtle py-2 last:border-b-0"
      >
        <span
          className="shrink-0 rounded bg-muted px-1.5 py-0.5 font-mono text-xs text-muted-foreground"
          data-testid={`field-mirror-type-${name}`}
        >
          {entry.type}
        </span>
        <span
          className="truncate text-xs"
          data-testid={`field-mirror-name-${name}`}
        >
          {name}
        </span>
      </div>
    );
  };

  return (
    <InspectorSection
      title={title}
      icon={mode === 'input' ? ArrowDownToLine : ArrowUpFromLine}
      testId={`fields-editor-${mode}`}
      actions={(
        <>
          <span className="rounded-md border border-edge-subtle bg-background px-1.5 py-0.5 text-xs tabular-nums text-content-tertiary">
            {names.length}
          </span>
          {!mirrored && (
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="h-7 w-7 shrink-0 text-focus hover:bg-background hover:text-focus"
            onClick={addEntry}
            disabled={readOnly}
            data-testid={`add-field-${mode}`}
            aria-label={t('inspector.fields.add', 'Add field')}
            title={t('inspector.fields.add', 'Add field')}
          >
            <Plus className="h-4 w-4" />
          </Button>
          )}
        </>
      )}
    >
      <div className="space-y-2">
        {mirrored && (
          <p className="text-xs text-muted-foreground" data-testid="outputs-mirror-caption">
            {t('inspector.fields.mirror_caption', 'Outputs mirror your inputs')}
          </p>
        )}

        {names.length === 0 ? (
          <p className="text-xs text-muted-foreground">
            {t('inspector.fields.empty', 'No fields.')}
          </p>
        ) : (
          <div>
            {names.map((n, i) =>
              mirrored
                ? renderMirrorCard(n)
                : mode === 'input'
                  ? renderInputCard(n, i)
                  : renderOutputCard(n, i),
            )}
          </div>
        )}
      </div>
    </InspectorSection>
  );
}
