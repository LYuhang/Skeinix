/**
 * CodeNode config editor.
 *
 * Engine schema: `{ programming_language, process_fn }`. Only Python is
 * supported today; the user code runs in a per-run worker subprocess. The
 * language enum is sourced from the backend's `programming_languages` list
 * so adding a new runtime is a server-side change.
 *
 * The `process_fn` body is rendered in a tall monospace textarea (min
 * 200px) per the plan. Commit-on-blur prevents the undo stack from
 * exploding while the user types.
 */
import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { History, Maximize2 } from 'lucide-react';
import { Label } from '@/components/ui/label';
import { Button } from '@/components/ui/button';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { python } from '@codemirror/lang-python';
import { indentUnit } from '@codemirror/language';
import type { Extension } from '@codemirror/state';
import { getEnumList, useEnums } from '@/lib/api/queries/enums';
import { PromptDiffDialog } from '@/components/modals/PromptDiffDialog';
import { useWorkflowVersions } from '@/lib/api/queries/workflow';
import { CodeMirrorField } from './CodeMirrorField';
import { ExpandedCodeMirrorDialog } from './ExpandedCodeMirrorDialog';
import type { NodeConfigEditorProps } from './types';

// Four-space indent unit: drives the Tab key, the `@codemirror/lang-python`
// auto-indent after `:` and the Enter-key re-indent (CodeMirror's default
// `indentUnit` is 2 spaces). Python convention is 4.
const PYTHON_EXTENSIONS: Extension[] = [indentUnit.of('    '), python()];

const FALLBACK_LANGUAGES = ['python'];

export function CodeNodeEditor({
  config,
  readOnly,
  onChange,
  nodeId,
  wfId,
}: NodeConfigEditorProps) {
  const { t } = useTranslation();
  const { data: enums } = useEnums();
  // process_fn version-diff modal. The "History" button only renders when this
  // node lives in a workflow with >= 2 versions (otherwise there's nothing to
  // compare). Mirrors PromptNodeEditor's prompt_template history.
  const [historyOpen, setHistoryOpen] = useState(false);
  const [expandedOpen, setExpandedOpen] = useState(false);
  const canShowHistory = !!wfId && !!nodeId;
  const versionsQuery = useWorkflowVersions(canShowHistory ? wfId : undefined);
  const versionCount = Array.isArray(
    (versionsQuery.data as { versions?: unknown[] } | undefined)?.versions,
  )
    ? (versionsQuery.data as { versions: unknown[] }).versions.length
    : 0;
  const showHistoryButton = canShowHistory && versionCount >= 2;
  const languages = useMemo(() => {
    const list = getEnumList(enums, 'programming_languages');
    return list.length > 0 ? list : FALLBACK_LANGUAGES;
  }, [enums]);

  const language =
    typeof config.programming_language === 'string'
      ? (config.programming_language as string)
      : 'python';
  const processFn =
    typeof config.process_fn === 'string'
      ? (config.process_fn as string)
      : '';

  return (
    <div className="space-y-3">
      <div className="space-y-1.5">
        <Label className="text-xs">{t('inspector.config.language', 'Language')}</Label>
        <Select
          value={language}
          onValueChange={(next) =>
            onChange({ ...config, programming_language: next })
          }
          disabled={readOnly}
        >
          <SelectTrigger className="h-8 text-xs">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {languages.map((l) => (
              <SelectItem key={l} value={l} className="text-xs">
                {l}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <div className="space-y-1.5">
        <div className="flex items-center justify-between">
          <Label className="text-xs">process_fn</Label>
          <div className="flex items-center gap-1">
            {showHistoryButton && (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="h-6 gap-1 px-2 text-xs text-muted-foreground"
                onClick={() => setHistoryOpen(true)}
                data-testid="cfg-code-history-btn"
              >
                <History className="h-3.5 w-3.5" />
                {t('prompt_history.button', 'History')}
              </Button>
            )}
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="h-6 w-6 text-muted-foreground"
              onClick={() => setExpandedOpen(true)}
              aria-label={t('inspector.config.expandEditor', 'Expand editor')}
              title={t('inspector.config.expandEditor', 'Expand editor')}
              data-testid="cfg-code-expand-btn"
            >
              <Maximize2 className="h-3.5 w-3.5" />
            </Button>
          </div>
        </div>
        <CodeMirrorField
          value={processFn}
          onCommit={(next) => onChange({ ...config, process_fn: next })}
          readOnly={readOnly}
          data-testid="cfg-code-fn"
          extensions={PYTHON_EXTENSIONS}
          minHeight="200px"
          placeholder={'def process_fn(inputs):\n    # your code here\n    return inputs'}
        />
      </div>

      <ExpandedCodeMirrorDialog
        open={expandedOpen}
        onOpenChange={setExpandedOpen}
        title="process_fn"
        meta={language}
        value={processFn}
        onCommit={(next) => onChange({ ...config, process_fn: next })}
        readOnly={readOnly}
        extensions={PYTHON_EXTENSIONS}
        placeholder={'def process_fn(inputs):\n    # your code here\n    return inputs'}
        testId="cfg-code-expanded-editor"
      />

      {showHistoryButton && wfId && nodeId && (
        <PromptDiffDialog
          open={historyOpen}
          onOpenChange={setHistoryOpen}
          wfId={wfId}
          nodeId={nodeId}
          currentPrompt={processFn}
          field="process_fn"
          title={t('code_history.title', 'Function history')}
          subtitle={t(
            'code_history.subtitle',
            'A historical version on the left, the current function on the right.',
          )}
        />
      )}
    </div>
  );
}
