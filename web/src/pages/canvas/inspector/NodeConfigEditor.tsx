/**
 * Per-node-type config editor dispatcher.
 *
 * The 14 engine node types each have a different `CONFIG_SCHEMA`; rather
 * than packing a thousand `if`s into one component, each type owns a
 * sibling file under `config-editors/`. This module is just the lookup
 * table: it picks the editor for the current `nodeType` and falls back
 * to a friendly "no config" placeholder for unknown types.
 *
 * Editors share the `NodeConfigEditorProps` contract (`config`, `readOnly`,
 * `onChange`); the dispatcher narrows the dispatch by `nodeType` and
 * forwards the rest unchanged. Adding a new node type is a two-step
 * change: write a new editor file + add it to the `EDITORS` map.
 */
import { useTranslation } from 'react-i18next';
import { Settings2 } from 'lucide-react';
import type { NodeConfigEditorProps } from './config-editors/types';
import { NODE_CONFIG_EDITORS } from './node-config-registry';
import { InspectorSection } from './InspectorSection';

/**
 * Node types with NO meaningful config to author. The inspector hides the
 * whole "Config" section for these (Change 3) rather than rendering an empty
 * block: StartNode's only field is the reserved/unused `process_fn`, and
 * EndNode's `CONFIG_SCHEMA` is empty. Unknown types (no entry in `EDITORS`)
 * are likewise treated as config-less by `nodeTypeHasConfig`.
 *
 * LoopEndNode is config-less from the USER's standpoint: its only config key
 * (`loop_begin_node_id`) is authored from the LoopBegin side (`loop_end_node_id`,
 * which pairs both ends atomically), so the LoopEnd inspector mirrors the
 * Parallel-end treatment — no fields, no config section. The `LoopEndNodeEditor`
 * file is retained (the pairing store action stays available) but the dispatcher
 * never renders it once LoopEndNode is config-less.
 */
function NullConfigEditor() {
  const { t } = useTranslation();
  return (
    <p className="text-xs text-muted-foreground">
      {t('inspector.config.nullEditor', 'No config editor for this node type.')}
    </p>
  );
}

export type NodeConfigEditorWrapperProps = {
  nodeType: string;
} & NodeConfigEditorProps;

export function NodeConfigEditor({
  nodeType,
  ...rest
}: NodeConfigEditorWrapperProps) {
  const { t } = useTranslation();
  const Editor = NODE_CONFIG_EDITORS[nodeType] ?? NullConfigEditor;
  return (
    <InspectorSection
      title={t('inspector.node.config', 'Config')}
      icon={Settings2}
      testId="inspector-section-config"
      actions={(
        <span className="rounded-md border border-edge-subtle bg-background px-1.5 py-0.5 font-mono text-xs text-content-tertiary">
          {nodeType}
        </span>
      )}
    >
      <Editor {...rest} />
    </InspectorSection>
  );
}
