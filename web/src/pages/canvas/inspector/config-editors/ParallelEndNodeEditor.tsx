/**
 * ParallelEndNode config editor — INTERACTIVE pairing mirror (Stream 3).
 *
 * Engine schema: `node_config.parallel_start_node_id` points back at the
 * paired ParallelStartNode. Editing it here calls the store's `pairNodes`
 * so BOTH sides update atomically (one undo step) and any stale old
 * partner is cleared — the mirror of the ParallelStart editor.
 */
import { useTranslation } from 'react-i18next';
import { Label } from '@/components/ui/label';
import { useWorkflowEditStore } from '@/stores/workflow-edit';
import type { NodeConfigEditorProps } from './types';
import { NodeRefSelect } from './NodeRefSelect';
import { listNodesOfType, useDraftGraph, useSelectedNodeId } from './node-graph';

export interface ParallelEndNodeEditorProps extends NodeConfigEditorProps {
  /** Override the selected node id (tests). */
  nodeId?: string;
}

export function ParallelEndNodeEditor({
  config,
  readOnly,
  onChange,
  nodeId,
}: ParallelEndNodeEditorProps) {
  const { t } = useTranslation();
  const graph = useDraftGraph();
  const selectedId = useSelectedNodeId();
  const thisId = nodeId ?? selectedId;
  const pairNodes = useWorkflowEditStore((s) => s.pairNodes);

  const starts = listNodesOfType(graph, 'ParallelStartNode');
  const parallelStart =
    typeof config.parallel_start_node_id === 'string'
      ? (config.parallel_start_node_id as string)
      : '';

  const onPair = (startId: string | null) => {
    if (!thisId) return;
    if (startId) pairNodes(thisId, startId, 'parallel');
    else onChange({ ...config, parallel_start_node_id: null });
  };

  return (
    <div className="space-y-1" data-testid="cfg-parallel-end">
      <Label className="text-xs">parallel_start_node_id</Label>
      <NodeRefSelect
        value={parallelStart}
        options={starts}
        onChange={onPair}
        disabled={readOnly}
        placeholder={t('inspector.config.parallel.selectStart', 'Select the paired ParallelStart')}
        data-testid="cfg-parallel-start-select"
      />
      <p className="text-xs text-muted-foreground">
        {t(
          'inspector.config.parallel.startHint',
          "Picks the paired ParallelStartNode; the partner's pointer is set automatically.",
        )}
      </p>
    </div>
  );
}
