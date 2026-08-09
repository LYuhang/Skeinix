/**
 * EndNode config editor.
 *
 * The engine's `EndNode.CONFIG_SCHEMA` is empty — there is nothing to edit.
 * We render a single line of explanatory text so the section is not
 * mysteriously blank.
 */
import { useTranslation } from 'react-i18next';

export function EndNodeEditor() {
  const { t } = useTranslation();
  return (
    <p className="text-xs text-muted-foreground">
      {t('inspector.config.endNone', 'No config for EndNode.')}
    </p>
  );
}
