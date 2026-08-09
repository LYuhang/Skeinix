/**
 * TemplateNode config editor.
 *
 * Engine schema (`engine/.../nodes/template.py`): `{ template: string,
 * output_format: 'html' | 'markdown' | 'text' }`. The `template` body is a
 * Jinja2 template rendered against the node's `inputs` ({{var}} interpolation
 * plus {% for %}/{% if %}/filters); `output_format` tells the downstream
 * renderer how to display the result. Media paths are emitted raw and the
 * frontend signs VFS paths at display time (no engine-side path rewrite).
 *
 * Layout mirrors PromptNode's prompt_template editor:
 *   - `output_format` is chosen FIRST (top), so the template editor's sample
 *     placeholder can show a format-appropriate starter.
 *   - the template body is a CodeMirror field with {{placeholder}} highlight +
 *     commit-on-blur, and a top-right "History" button opening a version-diff
 *     modal for the `template` field (the same `PromptDiffDialog`, generalized
 *     via its `field` prop).
 */
import { useState } from 'react';
import { History } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import type { Extension } from '@codemirror/state';
import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { PromptDiffDialog } from '@/components/modals/PromptDiffDialog';
import { useWorkflowVersions } from '@/lib/api/queries/workflow';
import { CodeMirrorField } from './CodeMirrorField';
import { placeholderHighlight, placeholderTheme } from './prompt-template';
import type { NodeConfigEditorProps } from './types';

/** Live {{placeholder}} highlight extensions (stable identity). */
const TEMPLATE_EXTENSIONS: Extension[] = [placeholderHighlight, placeholderTheme];

/** Engine enum (`CONFIG_SCHEMA.output_format`): html / markdown / text. */
const FORMATS = ['html', 'markdown', 'text'] as const;
type Format = (typeof FORMATS)[number];

/**
 * A real, helpful starter template per output format. Shown as the CodeMirror
 * background sample (placeholder) when the body is empty so the user sees what
 * a {{var}} template looks like in the chosen format.
 */
const SAMPLE_BY_FORMAT: Record<Format, string> = {
  html: '<h1>{{title}}</h1>\n<ul>\n{% for item in items %}\n  <li>{{item.name}}: {{item.score}}</li>\n{% endfor %}\n</ul>',
  markdown:
    '# {{title}}\n\n{% for item in items %}\n- **{{item.name}}**: {{item.score}}\n{% endfor %}',
  text: '{{title}}\n\n{% for item in items %}- {{item.name}}: {{item.score}}\n{% endfor %}',
};

export function TemplateNodeEditor({
  config,
  readOnly,
  onChange,
  nodeId,
  wfId,
}: NodeConfigEditorProps) {
  const { t } = useTranslation();

  const template =
    typeof config.template === 'string' ? (config.template as string) : '';
  const outputFormat: Format = FORMATS.includes(config.output_format as Format)
    ? (config.output_format as Format)
    : 'html';

  // template version-diff modal — only when the workflow has >= 2 versions
  // (mirrors PromptNode's showHistoryButton gate).
  const [historyOpen, setHistoryOpen] = useState(false);
  const canShowHistory = !!wfId && !!nodeId;
  const versionsQuery = useWorkflowVersions(canShowHistory ? wfId : undefined);
  const versionCount = Array.isArray(
    (versionsQuery.data as { versions?: unknown[] } | undefined)?.versions,
  )
    ? (versionsQuery.data as { versions: unknown[] }).versions.length
    : 0;
  const showHistoryButton = canShowHistory && versionCount >= 2;

  return (
    <div className="space-y-3">
      {/* 1. Pick the output format FIRST. */}
      <div className="space-y-1.5">
        <Label className="text-xs font-medium">
          {t('inspector.config.template.outputFormat', 'output_format')}
        </Label>
        <Select
          value={outputFormat}
          onValueChange={(next) => onChange({ ...config, output_format: next })}
          disabled={readOnly}
        >
          <SelectTrigger
            className="h-8 text-xs"
            data-testid="cfg-template-format-select"
          >
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {FORMATS.map((f) => (
              <SelectItem key={f} value={f} className="text-xs">
                {f}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {/* 2. Write the template — format-aware sample + {{var}} highlight. */}
      <div className="space-y-1.5">
        <div className="flex items-center justify-between">
          <Label className="text-xs font-medium">
            {t('inspector.config.template.template', 'template')}
            <span className="ml-1 font-normal text-muted-foreground">
              {t('inspector.config.template.jinjaLabel', '(Jinja template)')}
            </span>
          </Label>
          {showHistoryButton && (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="h-6 gap-1 px-2 text-xs text-muted-foreground"
              onClick={() => setHistoryOpen(true)}
              data-testid="cfg-template-history-btn"
            >
              <History className="h-3.5 w-3.5" />
              {t('prompt_history.button', 'History')}
            </Button>
          )}
        </div>
        <CodeMirrorField
          value={template}
          onCommit={(next) => onChange({ ...config, template: next })}
          readOnly={readOnly}
          data-testid="cfg-template-template"
          extensions={TEMPLATE_EXTENSIONS}
          minHeight="160px"
          placeholder={SAMPLE_BY_FORMAT[outputFormat]}
        />
        <p className="text-xs leading-tight text-muted-foreground">
          {t(
            'inspector.config.template.hint',
            'Jinja2 template — use double-brace interpolation and for/if control flow. Input fields are available as variables.',
          )}
        </p>
      </div>

      {showHistoryButton && wfId && nodeId && (
        <PromptDiffDialog
          open={historyOpen}
          onOpenChange={setHistoryOpen}
          wfId={wfId}
          nodeId={nodeId}
          currentPrompt={template}
          field="template"
          title={t('template_history.title', 'Template history')}
          subtitle={t(
            'template_history.subtitle',
            'A historical version on the left, the current template on the right.',
          )}
        />
      )}
    </div>
  );
}
