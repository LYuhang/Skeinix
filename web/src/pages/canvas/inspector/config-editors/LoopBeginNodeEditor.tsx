/**
 * LoopBeginNode config editor — INTERACTIVE (Stream 3).
 *
 * Engine schema: `{ init_value:{value,reference}, step_value:int>=1,
 * end_value:{value,reference}, loop_end_node_id }`. For init/end the engine
 * prefers a non-empty `reference` over the literal `value`.
 *
 *   - `init_value` / `end_value` use the shared `FieldValueWidget`
 *     (reference-or-preset; the value is an integer). The reference
 *     dropdown lists `producer_node_name.output_field` candidates — never
 *     free-text. Candidates are restricted to ANCESTOR (predecessor) nodes
 *     via `referenceCandidatesFromAncestors(draft, nodeId)` (the same
 *     ancestor-restriction the input-field pickers use in `NodeTab`), so init/end
 *     can only reference the output of a node that runs BEFORE the loop
 *     begins — never a successor / inside-the-loop node.
 *   - `step_value` is an int stepper (>= 1; a step of 0 never advances).
 *   - `loop_end_node_id` is a dropdown of LoopEndNodes; picking one calls
 *     the store's `pairNodes` so the partner's `loop_begin_node_id` is set
 *     atomically (one undo step, old partner cleared).
 *
 * Membership (the single body-head child) is owned by Stream 1; this editor
 * only edits config attrs + pairing.
 */
import { useTranslation } from 'react-i18next';
import { Label } from '@/components/ui/label';
import { useWorkflowEditStore } from '@/stores/workflow-edit';
import { CommitOnBlurNumber } from '@/pages/canvas/inspector/CommitOnBlur';
import {
  FieldValueWidget,
  type FieldSlotValue,
} from '@/pages/canvas/inspector/FieldValueWidget';
import { referenceCandidatesFromAncestors } from '@/lib/workflow/graph';
import type { NodeConfigEditorProps } from './types';
import { NodeRefSelect } from './NodeRefSelect';
import {
  listNodesOfType,
  useDraftGraph,
  useSelectedNodeId,
} from './node-graph';

interface ValueRefSlot {
  value?: unknown;
  reference?: string;
}

function readSlot(raw: unknown): ValueRefSlot {
  if (raw && typeof raw === 'object' && !Array.isArray(raw)) {
    return raw as ValueRefSlot;
  }
  return { value: '', reference: '' };
}

export interface LoopBeginNodeEditorProps extends NodeConfigEditorProps {
  /** Override the selected node id (tests). */
  nodeId?: string;
}

export function LoopBeginNodeEditor({
  config,
  readOnly,
  onChange,
  nodeId,
}: LoopBeginNodeEditorProps) {
  const { t } = useTranslation();
  const graph = useDraftGraph();
  const selectedId = useSelectedNodeId();
  const thisId = nodeId ?? selectedId;
  const pairNodes = useWorkflowEditStore((s) => s.pairNodes);

  const initValue = readSlot(config.init_value);
  const endValue = readSlot(config.end_value);
  const stepValue =
    typeof config.step_value === 'number' ? (config.step_value as number) : 1;
  const loopEnd =
    typeof config.loop_end_node_id === 'string'
      ? (config.loop_end_node_id as string)
      : '';

  const loopEnds = listNodesOfType(graph, 'LoopEndNode');
  // init/end may only reference the OUTPUT of an ANCESTOR (a node that runs
  // BEFORE the loop begins) — never a successor or a node inside the loop
  // body. Reuse the exact ancestor-restriction the input-field pickers use.
  const refCandidates = thisId
    ? referenceCandidatesFromAncestors(graph, thisId)
    : [];

  const setSlot = (key: 'init_value' | 'end_value') => (next: FieldSlotValue) => {
    onChange({
      ...config,
      [key]: { value: next.value, reference: next.reference ?? '' },
    });
  };

  const onPair = (endId: string | null) => {
    if (!thisId) return;
    if (endId) pairNodes(thisId, endId, 'loop');
    else onChange({ ...config, loop_end_node_id: null });
  };

  return (
    <div className="space-y-3" data-testid="cfg-loop-begin">
      <p
        className="text-xs leading-relaxed text-muted-foreground"
        data-testid="cfg-loop-semantics-hint"
      >
        {t(
          'inspector.config.loop.semanticsHint',
          'The counter i starts at init_value and increases by step_value each iteration. The loop runs WHILE i < end_value, so end_value is exclusive (NOT included).',
        )}
      </p>

      <div className="space-y-1">
        <Label className="text-xs">init_value</Label>
        <FieldValueWidget
          type="integer"
          value={initValue.value}
          reference={initValue.reference ?? ''}
          referenceCandidates={refCandidates}
          readOnly={readOnly}
          idBase="cfg-loop-init"
          onChange={setSlot('init_value')}
        />
      </div>

      <div className="space-y-1">
        <Label className="text-xs">step_value</Label>
        <CommitOnBlurNumber
          kind="int"
          step={1}
          min={1}
          value={stepValue}
          onCommit={(next) => onChange({ ...config, step_value: next })}
          disabled={readOnly}
          className="h-8 text-xs"
          data-testid="cfg-loop-step"
        />
      </div>

      <div className="space-y-1">
        <Label className="text-xs">end_value</Label>
        <FieldValueWidget
          type="integer"
          value={endValue.value}
          reference={endValue.reference ?? ''}
          referenceCandidates={refCandidates}
          readOnly={readOnly}
          idBase="cfg-loop-end-val"
          onChange={setSlot('end_value')}
        />
      </div>

      <div className="space-y-1">
        <Label className="text-xs">loop_end_node_id</Label>
        <NodeRefSelect
          value={loopEnd}
          options={loopEnds}
          onChange={onPair}
          disabled={readOnly}
          placeholder={t('inspector.config.loop.selectEnd', 'Select the paired LoopEnd')}
          data-testid="cfg-loop-end-select"
        />
        <p className="text-xs text-muted-foreground">
          {t(
            'inspector.config.loop.endHint',
            "Picks the paired LoopEndNode; the partner's pointer is set automatically.",
          )}
        </p>
      </div>
    </div>
  );
}
