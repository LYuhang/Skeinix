/**
 * SubAgentNode config editor.
 *
 * SubAgentNode embeds a bounded tool-using worker as a workflow node. Its
 * `node_config` shape (engine `nodes/subagent.py` CONFIG_SCHEMA):
 *
 *   {
 *     model_name: string,            // required
 *     task_template: string,         // required; supports {{field}} interpolation
 *     max_iterations?: number,
 *   }
 *
 * `onChange(next)` REPLACES the whole node_config; we treat `config` as
 * immutable and emit a fresh object on every edit.
 *
 * ## model_name
 *
 * Uses the same picker as PromptNode: the union of the tenant's
 * SAVED LLM credential NAMES (`useLlmCredentials`, label `name (provider)`,
 * value = bare NAME) and the built-in / platform models (`useModelOptions`,
 * the live `llm_registry`). Selecting a name stores ONLY the name; the api
 * injects the matching secret at EXECUTION time. We DON'T offer the inline
 * `custom_model_config` back-compat path here — the sub-model is always
 * resolved from a saved credential / registered model server-side.
 */
import { Label } from '@/components/ui/label';
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  CommitOnBlurInput,
  CommitOnBlurNumber,
} from '@/pages/canvas/inspector/CommitOnBlur';
import { useModelOptions } from '@/lib/api/queries/config-options';
import { useLlmCredentials } from '@/lib/api/queries/llm-credentials';
import { useTranslation } from 'react-i18next';
import { useState } from 'react';
import { History, Maximize2 } from 'lucide-react';
import type { Extension } from '@codemirror/state';
import { Button } from '@/components/ui/button';
import { PromptDiffDialog } from '@/components/modals/PromptDiffDialog';
import { useWorkflowVersions } from '@/lib/api/queries/workflow';
import { CodeMirrorField } from './CodeMirrorField';
import { ExpandedCodeMirrorDialog } from './ExpandedCodeMirrorDialog';
import { placeholderHighlight, placeholderTheme } from './prompt-template';
import type { NodeConfigEditorProps } from './types';

/** UI default for the optional engine cap. */
const DEFAULT_MAX_ITERATIONS = 25;
const TASK_TEMPLATE_EXTENSIONS: Extension[] = [placeholderHighlight, placeholderTheme];

export function SubAgentNodeEditor({
  config,
  readOnly,
  onChange,
  nodeId,
  wfId,
}: NodeConfigEditorProps) {
  const { t } = useTranslation();
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
  const { options: models } = useModelOptions();
  const { data: credentials } = useLlmCredentials();

  const taskTemplate =
    typeof config.task_template === 'string' ? (config.task_template as string) : '';
  const modelName =
    typeof config.model_name === 'string' ? (config.model_name as string) : '';
  const maxIterations =
    typeof config.max_iterations === 'number'
      ? (config.max_iterations as number)
      : DEFAULT_MAX_ITERATIONS;
  const emitConfig = (patch: Record<string, unknown>) => {
    const next: Record<string, unknown> = {
      task_template: taskTemplate,
      model_name: modelName,
      max_iterations: maxIterations,
      ...patch,
    };
    onChange(next);
  };

  // model picker surface (mirrors PromptNode): saved creds + built-ins.
  const savedCredentials = Array.isArray(credentials) ? credentials : [];
  const savedNames = new Set(savedCredentials.map((c) => c.name));
  const builtinModels = models.filter((m) => !savedNames.has(m));
  const isKnown = savedNames.has(modelName) || builtinModels.includes(modelName);
  const hasAnyOption = savedCredentials.length > 0 || builtinModels.length > 0;

  return (
    <div className="space-y-3">
      <div className="space-y-1.5">
        <div className="flex items-center justify-between">
          <Label className="text-xs font-medium">
            {t('subagent_node.task_template', 'Task template')}
          </Label>
          <div className="flex items-center gap-1">
            {showHistoryButton && (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="h-6 gap-1 px-2 text-xs text-muted-foreground"
                onClick={() => setHistoryOpen(true)}
                data-testid="cfg-subagent-task-history-btn"
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
              data-testid="cfg-subagent-task-expand-btn"
            >
              <Maximize2 className="h-3.5 w-3.5" />
            </Button>
          </div>
        </div>
        <CodeMirrorField
          value={taskTemplate}
          onCommit={(next) => emitConfig({ task_template: next })}
          readOnly={readOnly}
          extensions={TASK_TEMPLATE_EXTENSIONS}
          minHeight="160px"
          placeholder={t(
            'subagent_node.task_template_placeholder',
            '# Task\nRead {{file_path}} and summarize its key points.\n\n# Instructions\nUse file tools to inspect the file and extract important information.\n\n# Output\nReturn the declared output fields.',
          )}
          data-testid="cfg-subagent-task-template"
        />
        <ExpandedCodeMirrorDialog
          open={expandedOpen}
          onOpenChange={setExpandedOpen}
          title={t('subagent_node.task_template', 'Task template')}
          meta="task_template"
          value={taskTemplate}
          onCommit={(next) => emitConfig({ task_template: next })}
          readOnly={readOnly}
          extensions={TASK_TEMPLATE_EXTENSIONS}
          placeholder={t(
            'subagent_node.task_template_placeholder',
            '# Task\nRead {{file_path}} and summarize its key points.\n\n# Instructions\nUse file tools to inspect the file and extract important information.\n\n# Output\nReturn the declared output fields.',
          )}
          testId="cfg-subagent-task-expanded-editor"
        />
        {wfId && nodeId && (
          <PromptDiffDialog
            open={historyOpen}
            onOpenChange={setHistoryOpen}
            wfId={wfId}
            nodeId={nodeId}
            currentPrompt={taskTemplate}
            field="task_template"
            title={t('subagent_node.task_history_title', 'Task template history')}
            subtitle={t(
              'subagent_node.task_history_subtitle',
              'Compare this sub-agent task template across workflow versions.',
            )}
          />
        )}
      </div>

      <div className="space-y-1.5">
        <Label className="text-xs font-medium">
          {t('subagent_node.model', 'Model')}
        </Label>
        {!hasAnyOption ? (
          <CommitOnBlurInput
            value={modelName}
            onCommit={(next) => emitConfig({ model_name: next })}
            disabled={readOnly}
            placeholder={t(
              'subagent_node.model_placeholder',
              'Model name (no models configured)',
            )}
            className="h-8 text-xs"
            data-testid="cfg-subagent-model-input"
          />
        ) : (
          <Select
            value={modelName || undefined}
            onValueChange={(next) => emitConfig({ model_name: next })}
            disabled={readOnly}
          >
            <SelectTrigger className="h-8 text-xs" data-testid="cfg-subagent-model-select">
              <SelectValue
                placeholder={t('subagent_node.select_model', 'Select a model')}
              />
            </SelectTrigger>
            <SelectContent>
              {modelName && !isKnown && (
                <SelectItem value={modelName} className="text-xs">
                  {modelName} ({t('subagent_node.unavailable', 'unavailable')})
                </SelectItem>
              )}
              {savedCredentials.length > 0 && (
                <SelectGroup>
                  <SelectLabel className="text-xs">
                    {t('subagent_node.saved_api', 'Saved APIs')}
                  </SelectLabel>
                  {savedCredentials.map((c) => (
                    <SelectItem key={`cred-${c.id}`} value={c.name} className="text-xs">
                      {c.name} ({c.provider})
                    </SelectItem>
                  ))}
                </SelectGroup>
              )}
              {builtinModels.length > 0 && (
                <SelectGroup>
                  <SelectLabel className="text-xs">
                    {t('subagent_node.builtin', 'Built-in')}
                  </SelectLabel>
                  {builtinModels.map((m) => (
                    <SelectItem key={`builtin-${m}`} value={m} className="text-xs">
                      {m}
                    </SelectItem>
                  ))}
                </SelectGroup>
              )}
            </SelectContent>
          </Select>
        )}
      </div>

      <div className="space-y-1.5">
        <Label className="text-xs font-medium">
          {t('subagent_node.max_iterations', 'Max iterations')}
        </Label>
        <CommitOnBlurNumber
          kind="int"
          min={1}
          step={1}
          value={maxIterations}
          onCommit={(next) => emitConfig({ max_iterations: next })}
          disabled={readOnly}
          className="h-8 text-xs"
          data-testid="cfg-subagent-max-iterations"
        />
        <p className="text-xs leading-tight text-muted-foreground">
          {t(
            'subagent_node.max_iterations_hint',
            'Upper bound on the sub-agent reasoning/tool steps before it must finish.',
          )}
        </p>
      </div>
    </div>
  );
}
