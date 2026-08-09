/**
 * StartNode config editor.
 *
 * StartNode has no node_config — the engine requires it to be empty (`{}`),
 * so any field here would make validation fail. The workflow's input
 * interface is defined by the node's input_fields (edited in the fields
 * section), not here. We render a single explanatory line.
 */
import { useTranslation } from 'react-i18next';

export function StartNodeEditor() {
  const { t } = useTranslation();
  return (
    <p className="text-xs text-muted-foreground">
      {t(
        'inspector.config.startNone',
        'No config for StartNode. Define the workflow inputs in the fields above.',
      )}
    </p>
  );
}
