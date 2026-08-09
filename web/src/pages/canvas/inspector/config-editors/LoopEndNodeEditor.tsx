/**
 * LoopEndNode config editor — INTERACTIVE pairing mirror (Stream 3).
 *
 * Engine schema: `node_config.loop_begin_node_id` points back at the
 * paired LoopBeginNode. Editing it here calls the store's `pairNodes`
 * (kind `'loop'`) so BOTH sides update atomically and the old partner is
 * cleared — the mirror of the LoopBegin editor.
 */
import { useTranslation } from 'react-i18next';
import { Label } from '@/components/ui/label';
import { useWorkflowEditStore } from '@/stores/workflow-edit';
import type { NodeConfigEditorProps } from './types';
import { NodeRefSelect } from './NodeRefSelect';
import { listNodesOfType, useDraftGraph, useSelectedNodeId } from './node-graph';

export interface LoopEndNodeEditorProps extends NodeConfigEditorProps {
  /** Override the selected node id (tests). */
  nodeId?: string;
}

export function LoopEndNodeEditor({
  config,
  readOnly,
  onChange,
  nodeId,
}: LoopEndNodeEditorProps) {
  const { t } = useTranslation();
  const graph = useDraftGraph();
  const selectedId = useSelectedNodeId();
  const thisId = nodeId ?? selectedId;
  const pairNodes = useWorkflowEditStore((s) => s.pairNodes);

  const begins = listNodesOfType(graph, 'LoopBeginNode');
  const loopBegin =
    typeof config.loop_begin_node_id === 'string'
      ? (config.loop_begin_node_id as string)
      : '';

  const onPair = (beginId: string | null) => {
    if (!thisId) return;
    if (beginId) pairNodes(thisId, beginId, 'loop');
    else onChange({ ...config, loop_begin_node_id: null });
  };

  return (
    <div className="space-y-1" data-testid="cfg-loop-end">
      <Label className="text-xs">loop_begin_node_id</Label>
      <NodeRefSelect
        value={loopBegin}
        options={begins}
        onChange={onPair}
        disabled={readOnly}
        placeholder={t('inspector.config.loop.selectBegin', 'Select the paired LoopBegin')}
        data-testid="cfg-loop-begin-select"
      />
      <p className="text-xs text-muted-foreground">
        {t(
          'inspector.config.loop.beginHint',
          "Picks the paired LoopBeginNode; the partner's pointer is set automatically.",
        )}
      </p>
    </div>
  );
}
