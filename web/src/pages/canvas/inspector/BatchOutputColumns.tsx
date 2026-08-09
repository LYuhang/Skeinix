/**
 * Batch-run output-column configuration.
 *
 * The batch output table is built from an explicit, ordered list of columns —
 * the table is EXACTLY these columns, left-to-right. This card list owns the
 * editor state and hands BatchTab the final ordered array to put in the submit
 * body's `output_columns`.
 *
 * Layout:
 *   - 4 FIXED cards pinned at the top, in order: index, status, error,
 *     execution_time.
 *     Non-deletable, not draggable, kind is fixed; only the column header
 *     (`name`) is editable.
 *   - USER cards below — each has an editable `name`, a SOURCE dropdown (a node
 *     output field), an optional `default`, a × delete, and a drag handle.
 *     "Add column" appends a blank field card. User cards reorder among
 *     themselves via native HTML5 drag-and-drop (the fixed cards stay pinned).
 *
 * The wire order BatchTab sends = [fixed columns in fixed order] + [user cards in
 * their current order]. See `toWireColumns` for the fixed→wire + user→wire
 * mapping and the skip-incomplete-field-card rule.
 */
import { useMemo, type Dispatch, type SetStateAction } from 'react';
import { useTranslation } from 'react-i18next';
import { GripVertical, X } from 'lucide-react';

import type { OutputFieldCandidate } from '@/lib/workflow/output-fields';
import { SearchSelect, type SearchSelectOption } from '@/components/ui/search-select';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  decodeSource,
  encodeSource,
  FIXED_KINDS,
  makeUserColumn,
  reorderUserColumns,
  type BatchColumnsState,
  type FixedKind,
  type UserColumn,
} from './batch-output-columns-model';

/** A user-added (non-fixed) column being edited. `node`/`field` empty until a
 *  source is chosen. `id` is a stable client key for list + DnD. */
const NONE_VALUE = '__none__';

export interface BatchOutputColumnsProps {
  state: BatchColumnsState;
  /**
   * React-style setter (`useState` dispatch). All mutators below pass a
   * FUNCTIONAL updater so back-to-back edits compose on the latest state —
   * critical when two changes (e.g. pick a source, then type a header) fire
   * within one render without a re-render between them.
   */
  onChange: Dispatch<SetStateAction<BatchColumnsState>>;
  candidates: OutputFieldCandidate[];
}

export function BatchOutputColumns({
  state,
  onChange,
  candidates,
}: BatchOutputColumnsProps) {
  const { t } = useTranslation();
  const sourceOptions = useMemo<SearchSelectOption[]>(
    () => [
      {
        value: NONE_VALUE,
        label: t('canvas.batch.colPickSource', '— pick a node output —'),
        keywords: ['none'],
      },
      ...candidates.map((cand) => ({
        value: encodeSource(cand.node, cand.field),
        label: cand.label,
        keywords: [cand.node, cand.field, cand.label],
      })),
    ],
    [candidates, t],
  );

  const setFixedName = (kind: FixedKind, name: string) =>
    onChange((s) => ({ ...s, fixedNames: { ...s.fixedNames, [kind]: name } }));

  const updateUser = (id: string, patch: Partial<UserColumn>) =>
    onChange((s) => ({
      ...s,
      userColumns: s.userColumns.map((c) =>
        c.id === id ? { ...c, ...patch } : c,
      ),
    }));

  const removeUser = (id: string) =>
    onChange((s) => ({
      ...s,
      userColumns: s.userColumns.filter((c) => c.id !== id),
    }));

  const addUser = () =>
    onChange((s) => ({ ...s, userColumns: [...s.userColumns, makeUserColumn()] }));

  const moveUser = (from: number, to: number) =>
    onChange((s) => ({
      ...s,
      userColumns: reorderUserColumns(s.userColumns, from, to),
    }));

  const fixedLabel = (kind: FixedKind): string =>
    ({
      execution_time: t('canvas.batch.colKindExecutionTime', 'Execution time'),
      index: t('canvas.batch.colKindIndex', 'Row index'),
      status: t('canvas.batch.colKindStatus', 'Status'),
      error: t('canvas.batch.colKindError', 'Error'),
    })[kind];

  return (
    <div className="space-y-2" data-testid="batch-output-columns">
      <div>
        <span className="text-sm font-medium">
          {t('canvas.batch.outputColumns', 'Output columns')}
        </span>
        <p className="text-xs text-muted-foreground">
          {t(
            'canvas.batch.outputColumnsHint',
            'The output table is exactly these columns, in order. Fixed metadata stays compact; add columns to pull selected node outputs.',
          )}
        </p>
      </div>

      {/* Fixed cards — pinned, non-deletable, not draggable. */}
      {FIXED_KINDS.map((kind) => (
        <div
          key={kind}
          className="border-b border-edge-subtle py-2 last:border-b-0"
          data-testid={`batch-col-fixed-${kind}`}
        >
          <div className="mb-1 flex items-center gap-2">
            <span className="text-[13px] font-medium text-content-secondary">
              {fixedLabel(kind)}
            </span>
          </div>
          <Input
            type="text"
            value={state.fixedNames[kind]}
            onChange={(e) => setFixedName(kind, e.target.value)}
            placeholder={kind}
            aria-label={t('canvas.batch.colHeader', 'Column header')}
            data-testid={`batch-col-fixed-name-${kind}`}
            className="h-8 w-full text-sm"
          />
        </div>
      ))}

      {/* User cards — draggable to reorder among themselves. */}
      {state.userColumns.map((c, i) => (
        <div
          key={c.id}
          draggable
          onDragStart={(e) => {
            e.dataTransfer.setData('text/plain', String(i));
            e.dataTransfer.effectAllowed = 'move';
          }}
          onDragOver={(e) => {
            e.preventDefault();
            e.dataTransfer.dropEffect = 'move';
          }}
          onDrop={(e) => {
            e.preventDefault();
            const from = Number(e.dataTransfer.getData('text/plain'));
            if (Number.isFinite(from)) moveUser(from, i);
          }}
          className="space-y-2 border-b border-edge-subtle py-2 last:border-b-0"
          data-testid="batch-col-user"
          data-col-id={c.id}
        >
          <div className="flex items-center gap-2">
            <span
              className="flex h-8 w-6 cursor-grab items-center justify-center text-muted-foreground"
              aria-label={t('canvas.batch.colDragHandle', 'Drag to reorder')}
              data-testid="batch-col-drag"
            >
              <GripVertical className="h-4 w-4" />
            </span>
            <Input
              type="text"
              value={c.name}
              onChange={(e) => updateUser(c.id, { name: e.target.value })}
              placeholder={t('canvas.batch.colHeaderPlaceholder', 'Column header')}
              aria-label={t('canvas.batch.colHeader', 'Column header')}
              data-testid="batch-col-name"
              className="h-8 flex-1 text-sm"
            />
            <Button
              type="button"
              onClick={() => removeUser(c.id)}
              aria-label={t('canvas.batch.colDelete', 'Delete column')}
              data-testid="batch-col-delete"
              variant="quiet"
              size="icon-sm"
            >
              <X className="h-4 w-4" />
            </Button>
          </div>

          <SearchSelect
            value={c.node && c.field ? encodeSource(c.node, c.field) : NONE_VALUE}
            options={[
              ...sourceOptions,
              ...(c.node &&
                c.field &&
                !candidates.some((cand) => cand.node === c.node && cand.field === c.field)
                ? [
                    {
                      value: encodeSource(c.node, c.field),
                      label: t('canvas.batch.colStaleSource', '{{label}} (missing)', {
                        label: `${c.node}.${c.field}`,
                      }),
                      keywords: [c.node, c.field],
                    },
                  ]
                : []),
            ]}
            onValueChange={(value) =>
              updateUser(c.id, decodeSource(value === NONE_VALUE ? '' : value))
            }
            placeholder={t('canvas.batch.colPickSource', '— pick a node output —')}
            searchPlaceholder={t('canvas.batch.searchNodeOutput', 'Search node outputs')}
            emptyText={t('canvas.batch.noNodeOutputMatches', 'No node outputs match your search.')}
            triggerClassName="h-8 text-sm"
            triggerTestId="batch-col-source"
          />

          <Input
            type="text"
            value={c.default}
            onChange={(e) => updateUser(c.id, { default: e.target.value })}
            placeholder={t('canvas.batch.colDefaultPlaceholder', 'Default (optional)')}
            aria-label={t('canvas.batch.colDefault', 'Default value')}
            data-testid="batch-col-default"
            className="h-8 w-full text-sm"
          />
        </div>
      ))}

      <Button
        type="button"
        onClick={addUser}
        data-testid="batch-col-add"
        variant="outline"
        size="sm"
        className="w-full border-dashed text-muted-foreground"
      >
        {t('canvas.batch.colAdd', '+ Add column')}
      </Button>
    </div>
  );
}
