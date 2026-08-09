/**
 * `FieldValueWidget` — the shared, type-aware "reference OR preset value"
 * editor for a single field slot.
 *
 * Used by:
 *   - the inspector field cards (Stream 5 — input field value),
 *   - execution side panels (workflow/node input forms),
 *   - LoopBegin init/end value slots (Stream 3).
 *
 * --------------------------------------------------------------------------
 * PUBLIC API (the contract Stream 2 / Stream 3 reuse)
 * --------------------------------------------------------------------------
 * A field slot on the wire is `{ type, value?, reference? }`. The widget
 * does NOT own the slot — it is fully controlled:
 *
 *   <FieldValueWidget
 *     type="number"                       // drives the preset widget shape
 *     value={entry.value}                 // current literal (any JSON value)
 *     reference={entry.reference ?? ''}    // current reference string
 *     referenceCandidates={[              // dropdown options (NOT free text)
 *       'start.user_query', 'fetch.rows', ...
 *     ]}
 *     readOnly={false}
 *     idBase="field-foo"                  // prefix for data-testid / aria
 *     allowReference                      // omit (false) to force preset-only
 *                                          // (the Execute dialog has no graph
 *                                          //  producers to reference)
 *     onChange={(next) => set({ value: next.value, reference: next.reference })}
 *   />
 *
 * `onChange` always receives BOTH keys: `{ value, reference }`.
 *   - Reference mode  → `{ value: <preserved>, reference: <selected string> }`
 *   - Preset mode     → `{ value: <literal or raw buffer>, reference: '' }`
 * (We never write a space-sentinel; `''` is the legal "no reference" form
 * per `Workflow.check`.)
 *
 * Mode is DERIVED, never stored: `reference` truthy ⇒ reference mode. The
 * caller (a card / dialog row) keys the widget on the field identity so any
 * transient buffer resets across selected-node changes.
 *
 * Two pure helpers are exported for callers that still want local coercion
 * (regular field editing) and unit tests:
 *   - `coerceValueForType(raw, type)` → the engine-shaped JS value (or throws
 *     a `FieldCoercionError` on un-parseable object/array/number JSON).
 *   - `valueToDisplayString(value, type)` → the buffer string the widget shows.
 */
import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Maximize2 } from 'lucide-react';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {
  coerceValueForType,
  FieldCoercionError,
  isJsonType,
  isNumberType,
  normalizeFieldType,
  valueToDisplayString,
} from './field-value-model';

export interface FieldSlotValue {
  value?: unknown;
  reference?: string;
}

export interface FieldValueWidgetProps {
  /** Drives the preset widget shape (number/integer/boolean/object/array/string). */
  type: string;
  value?: unknown;
  reference?: string;
  /** `producer_node_name.output_field` options for the reference dropdown. */
  referenceCandidates?: string[];
  /** When false, the Reference toggle is hidden (preset-only). Default true. */
  allowReference?: boolean;
  /** Keep the parent buffer as raw user input; backend can normalize on submit. */
  deferCoercion?: boolean;
  readOnly?: boolean;
  /** Prefix for data-testid + aria-label so multiple widgets stay distinct. */
  idBase: string;
  onChange: (next: FieldSlotValue) => void;
}

/**
 * Coerce a raw widget value to its engine-shaped JS value.
 *
 * Regular field editing may use this to persist typed JSON values in the
 * workflow draft. Execution side panels can set `deferCoercion` and submit raw
 * strings to the backend execution boundary, where field-schema normalization
 * is applied once before the engine runs.
 *
 *   - number/integer/float → `Number` (throws on NaN)
 *   - boolean              → JS boolean (accepts a bool or "true"/"false")
 *   - object/array         → `JSON.parse` (throws on malformed)
 *   - string / unknown     → `String`
 *
 * `raw` is typically the buffer string the user typed, but already-typed
 * values (e.g. a boolean from a checkbox) pass through coherently.
 */
export function FieldValueWidget({
  type,
  value,
  reference,
  referenceCandidates = [],
  allowReference = true,
  deferCoercion = false,
  readOnly = false,
  idBase,
  onChange,
}: FieldValueWidgetProps) {
  const { t } = useTranslation();
  const isRef = allowReference && Boolean(reference);

  // Inline parse-error message for object/array preset values. Cleared on a
  // successful commit. Non-authoritative + ephemeral (Stream 0b).
  const [jsonError, setJsonError] = useState<string | null>(null);

  const candidates = useMemo(
    () => referenceCandidates.filter((c) => c.length > 0),
    [referenceCandidates],
  );

  const toggleMode = (next: boolean) => {
    if (readOnly) return;
    setJsonError(null);
    if (next) {
      // → reference mode: preserve the literal so toggling back restores it.
      onChange({ value: value ?? '', reference: candidates[0] ?? '' });
    } else {
      // → preset mode: clear the reference.
      onChange({ value: value ?? '', reference: '' });
    }
  };

  const commitPreset = (raw: unknown) => {
    if (deferCoercion) {
      setJsonError(null);
      onChange({ value: raw, reference: '' });
      return;
    }
    try {
      const coerced = coerceValueForType(raw, type);
      setJsonError(null);
      onChange({ value: coerced, reference: '' });
    } catch (err) {
      if (err instanceof FieldCoercionError) {
        setJsonError(err.message);
      } else {
        throw err;
      }
    }
  };

  return (
    <div className="space-y-1" data-testid={`${idBase}-value`}>
      {allowReference && (
        <div className="flex items-center justify-between">
          <Label className="text-xs text-muted-foreground">
            {isRef
              ? t('inspector.fieldValue.reference', 'Reference')
              : t('inspector.fieldValue.preset', 'Preset value')}
          </Label>
          <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <span>{t('inspector.fieldValue.useReference', 'Use reference')}</span>
            <Switch
              data-testid={`${idBase}-ref-toggle`}
              aria-label={`use reference ${idBase}`}
              checked={isRef}
              onCheckedChange={toggleMode}
              disabled={readOnly}
            />
          </label>
        </div>
      )}

      {isRef ? (
        <select
          data-testid={`${idBase}-ref-select`}
          aria-label={`reference ${idBase}`}
          className="h-8 w-full rounded-md border border-input bg-background px-2 text-xs"
          value={typeof reference === 'string' ? reference : ''}
          disabled={readOnly}
          onChange={(e) => onChange({ value: value ?? '', reference: e.target.value })}
        >
          <option value="">
            {t('inspector.fieldValue.selectProducer', '— select a producer output —')}
          </option>
          {/* Keep a stale reference visible even if its producer vanished. */}
          {reference && !candidates.includes(reference) && (
            <option value={reference}>
              {t('inspector.fieldValue.refMissing', '{{ref}} (missing)', {
                ref: reference,
              })}
            </option>
          )}
          {candidates.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
      ) : (
        <PresetInput
          type={type}
          value={value}
          deferCoercion={deferCoercion}
          readOnly={readOnly}
          idBase={idBase}
          onCommit={commitPreset}
        />
      )}

      {jsonError && (
        <p className="text-xs text-destructive" data-testid={`${idBase}-error`}>
          {jsonError}
        </p>
      )}
    </div>
  );
}

interface PresetInputProps {
  type: string;
  value: unknown;
  deferCoercion: boolean;
  readOnly: boolean;
  idBase: string;
  onCommit: (raw: unknown) => void;
}

function PresetInput({
  type,
  value,
  deferCoercion,
  readOnly,
  idBase,
  onCommit,
}: PresetInputProps) {
  const normalizedType = normalizeFieldType(type);
  if (normalizedType === 'boolean') {
    const checked = value === true || value === 'true';
    return (
      <div className="flex h-8 items-center">
        <Switch
          data-testid={`${idBase}-checkbox`}
          aria-label={`value ${idBase}`}
          checked={checked}
          disabled={readOnly}
          onCheckedChange={(c) => onCommit(c)}
        />
      </div>
    );
  }

  if (isJsonType(normalizedType)) {
    return (
      <ExpandableValueInput
        idBase={idBase}
        type={normalizedType}
        value={value}
        deferCoercion={deferCoercion}
        readOnly={readOnly}
        onCommit={onCommit}
        multiline
      />
    );
  }

  // number / integer / string → a commit-on-blur text input. We keep ONE
  // text input (rather than a native number input) so the parse guard +
  // coercion live in one place; type still drives the inputMode hint.
  return (
    <ExpandableValueInput
      idBase={idBase}
      type={type}
      value={value}
      deferCoercion={deferCoercion}
      readOnly={readOnly}
      onCommit={onCommit}
      multiline={false}
    />
  );
}

function ExpandableValueInput({
  idBase,
  type,
  value,
  deferCoercion,
  readOnly,
  multiline,
  onCommit,
}: {
  idBase: string;
  type: string;
  value: unknown;
  deferCoercion: boolean;
  readOnly: boolean;
  multiline: boolean;
  onCommit: (raw: unknown) => void;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const display = valueToDisplayString(value, type);
  const [draftState, setDraftState] = useState({ source: display, value: display });
  const draft = draftState.source === display ? draftState.value : display;
  const setDraft = (next: string) => setDraftState({ source: display, value: next });

  const commitDraft = () => onCommit(draft);
  const normalizedType = normalizeFieldType(type);
  const placeholder = isJsonType(normalizedType)
    ? normalizedType === 'array' ? '["item"]' : '{"key": "value"}'
    : isNumberType(type) ? '0' : 'value';

  return (
    <div className="relative">
      {multiline ? (
        <textarea
          data-testid={`${idBase}-json`}
          aria-label={`value ${idBase}`}
          className="max-h-40 min-h-[72px] w-full rounded-md border border-input bg-background px-2 py-1 pr-8 font-mono text-xs"
          value={draft}
          disabled={readOnly}
          placeholder={placeholder}
          onChange={(e) => {
            const next = e.target.value;
            setDraft(next);
            if (deferCoercion) onCommit(next);
          }}
          onBlur={commitDraft}
        />
      ) : (
        <input
          data-testid={`${idBase}-input`}
          aria-label={`value ${idBase}`}
          value={draft}
          onChange={(e) => {
            const next = e.target.value;
            setDraft(next);
            if (deferCoercion) onCommit(next);
          }}
          onBlur={commitDraft}
          disabled={readOnly}
          inputMode={isNumberType(type) ? 'decimal' : undefined}
          placeholder={placeholder}
          className="h-8 w-full rounded-md border border-input bg-background px-2 py-1 pr-8 text-xs outline-none focus:ring-1 focus:ring-ring disabled:cursor-not-allowed disabled:opacity-50"
        />
      )}
      <Button
        type="button"
        variant="ghost"
        size="icon"
        className="absolute right-1 top-1 h-6 w-6 text-muted-foreground hover:text-foreground"
        disabled={readOnly}
        aria-label={t('inspector.fieldValue.expand', 'Expand editor')}
        title={t('inspector.fieldValue.expand', 'Expand editor')}
        data-testid={`${idBase}-expand`}
        onClick={() => {
          setOpen(true);
        }}
      >
        <Maximize2 className="h-3.5 w-3.5" />
      </Button>
      <Dialog
        open={open}
        onOpenChange={(next) => {
          if (!next) commitDraft();
          setOpen(next);
        }}
      >
        <DialogContent className="max-w-3xl">
          <DialogHeader>
            <DialogTitle className="text-base">
              {t('inspector.fieldValue.editValue', 'Edit value')}
            </DialogTitle>
            <DialogDescription>
              {t('inspector.fieldValue.editValueDesc', 'Changes are applied back to the input field when this editor closes.')}
            </DialogDescription>
          </DialogHeader>
          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            disabled={readOnly}
            placeholder={placeholder}
            className="h-[52vh] min-h-[280px] w-full resize-none rounded-md border border-input bg-background p-3 font-mono text-sm leading-6 outline-none focus:ring-1 focus:ring-ring"
            data-testid={`${idBase}-expanded-editor`}
          />
        </DialogContent>
      </Dialog>
    </div>
  );
}
