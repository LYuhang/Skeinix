/**
 * ParallelStartNode config editor — INTERACTIVE (Stream 3).
 *
 * Engine schema: `node_config.branches` is a dict
 * `{ branchName: { branch_description, next_node_id } }` plus a
 * `parallel_end_node_id` pointing at the paired ParallelEndNode.
 *
 * Ownership split (state-ownership rule):
 *   - **Membership** (which `next_node_id`s exist + the children invariant)
 *     is owned by Stream 1's `connectNodes`/`disconnectNodes` via
 *     `syncTypeConfigOnEdgeChange`. This editor NEVER adds/removes a branch
 *     target — it only edits the **editable attrs** (`branch_description`)
 *     and re-targets a branch among the node's EXISTING children.
 *   - **Pairing** (`parallel_end_node_id` ↔ `parallel_start_node_id`) is set
 *     atomically through the store's `pairNodes`, so both sides update in
 *     ONE undo step and the old partner's back-pointer is cleared.
 *
 * `branch_description` commits through `onChange` (config write seam). The
 * `next_node_id` dropdown lists the node's current children (labeled by
 * node_name); the `parallel_end_node_id` dropdown lists ParallelEndNodes.
 */
import { useTranslation } from 'react-i18next';
import { Label } from '@/components/ui/label';
import { useWorkflowEditStore } from '@/stores/workflow-edit';
import { CommitOnBlurInput } from '@/pages/canvas/inspector/CommitOnBlur';
import type { NodeConfigEditorProps } from './types';
import { NodeRefSelect } from './NodeRefSelect';
import {
  childrenOf,
  listNodesOfType,
  useDraftGraph,
  useSelectedNodeId,
} from './node-graph';

interface BranchEntry {
  branch_description?: string;
  next_node_id?: string | null;
}

export interface ParallelStartNodeEditorProps extends NodeConfigEditorProps {
  /** Override the selected node id (tests). Falls back to xyflow selection. */
  nodeId?: string;
}

export function ParallelStartNodeEditor({
  config,
  readOnly,
  onChange,
  nodeId,
}: ParallelStartNodeEditorProps) {
  const { t } = useTranslation();
  const graph = useDraftGraph();
  const selectedId = useSelectedNodeId();
  const thisId = nodeId ?? selectedId;
  const pairNodes = useWorkflowEditStore((s) => s.pairNodes);

  const branches =
    config.branches && typeof config.branches === 'object'
      ? (config.branches as Record<string, BranchEntry>)
      : {};
  const branchNames = Object.keys(branches);
  const childRefs = childrenOf(graph, thisId);
  const parallelEnds = listNodesOfType(graph, 'ParallelEndNode');
  const parallelEnd =
    typeof config.parallel_end_node_id === 'string'
      ? (config.parallel_end_node_id as string)
      : '';

  const updateBranch = (name: string, patch: Partial<BranchEntry>) => {
    const next = { ...branches, [name]: { ...branches[name], ...patch } };
    onChange({ ...config, branches: next });
  };

  const onPair = (endId: string | null) => {
    if (!thisId) return;
    if (endId) pairNodes(thisId, endId, 'parallel');
    else onChange({ ...config, parallel_end_node_id: null });
  };

  return (
    <div className="space-y-3" data-testid="cfg-parallel-start">
      <div className="space-y-1.5">
        <Label className="text-xs">branches</Label>
        {branchNames.length === 0 ? (
          <p className="text-xs text-muted-foreground">
            {t(
              'inspector.config.parallel.empty',
              'No branches yet — connect this node to branch heads on the canvas.',
            )}
          </p>
        ) : (
          <ul className="space-y-2">
            {branchNames.map((name) => {
              const b = branches[name];
              return (
                <li
                  key={name}
                  className="space-y-1 rounded border bg-muted/30 px-2 py-1.5"
                  data-testid={`cfg-branch-${name}`}
                >
                  <div className="text-xs font-medium">{name}</div>
                  <CommitOnBlurInput
                    value={b.branch_description ?? ''}
                    onCommit={(next) =>
                      updateBranch(name, { branch_description: next })
                    }
                    disabled={readOnly}
                    placeholder={t('inspector.config.parallel.descPlaceholder', 'branch description')}
                    className="h-8 text-xs"
                    data-testid={`cfg-branch-desc-${name}`}
                  />
                  <NodeRefSelect
                    value={b.next_node_id}
                    options={childRefs}
                    onChange={(next) =>
                      updateBranch(name, { next_node_id: next })
                    }
                    disabled={readOnly}
                    placeholder={t('inspector.config.parallel.targetPlaceholder', 'target (a child node)')}
                    data-testid={`cfg-branch-target-${name}`}
                  />
                </li>
              );
            })}
          </ul>
        )}
        <p className="text-xs text-muted-foreground">
          {t(
            'inspector.config.parallel.branchHint',
            "Add/remove branches by drawing edges on the canvas. The branch targets must match this node's children.",
          )}
        </p>
      </div>

      <div className="space-y-1">
        <Label className="text-xs">parallel_end_node_id</Label>
        <NodeRefSelect
          value={parallelEnd}
          options={parallelEnds}
          onChange={onPair}
          disabled={readOnly}
          placeholder={t('inspector.config.parallel.selectEnd', 'Select the paired ParallelEnd')}
          data-testid="cfg-parallel-end-select"
        />
        <p className="text-xs text-muted-foreground">
          {t(
            'inspector.config.parallel.endHint',
            "Picks the paired ParallelEndNode; the partner's back-pointer is set automatically.",
          )}
        </p>
      </div>
    </div>
  );
}
