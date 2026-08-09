/**
 * PromptNode config editor.
 *
 * Engine schema (`engine/.../nodes/prompt.py`) requires `prompt_template`,
 * `model_name`, and an `inference_config` object with EXACTLY these four
 * keys: `temperature`, `max_tokens`, `top_k`, `top_p`
 * (`additionalProperties: False`). We always render all four rows so a
 * freshly-created PromptNode passes `Workflow.check` without the user
 * having to know which knobs are mandatory.
 *
 * Defaults (engine-aligned, used when the corresponding key is absent):
 *   temperature: 1.0   max_tokens: 512   top_k: -1   top_p: 0.9
 *
 * On any edit we spread `...inference` so unknown / future keys are
 * preserved (defensive against schema drift, e.g. a `seed` knob added
 * server-side).
 *
 * ## model_name
 *
 * The dropdown options are the union of:
 *   - the user's SAVED LLM credential NAMES (API Management Center, 1a) —
 *     label shows `name (provider)`, value is the bare NAME. Selecting one
 *     stores only the NAME; the api injects the matching secret config into
 *     the engine run at EXECUTION time (`extra.llm_credentials`). The
 *     frontend / node_config NEVER hold the api_key.
 *   - the built-in custom providers "OpenAI" / "Gemini" + any platform models
 *     from `useModelOptions()` (the live `llm_registry`).
 *
 * When the user picks "OpenAI"/"Gemini" the inline `custom_model_config`
 * fields appear as a back-compat path (enter your own model/key/url inline).
 * When a SAVED name is selected those fields are hidden — resolution happens
 * server-side at runtime.
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
  CommitOnBlurTextarea,
} from '@/pages/canvas/inspector/CommitOnBlur';
import { useModelOptions } from '@/lib/api/queries/config-options';
import { useLlmCredentials } from '@/lib/api/queries/llm-credentials';
import { useTranslation } from 'react-i18next';
import { useMemo, useState } from 'react';
import { History, Maximize2 } from 'lucide-react';
import type { Extension } from '@codemirror/state';
import { Button } from '@/components/ui/button';
import { PromptDiffDialog } from '@/components/modals/PromptDiffDialog';
import { useWorkflowVersions } from '@/lib/api/queries/workflow';
import { CodeMirrorField } from './CodeMirrorField';
import { ExpandedCodeMirrorDialog } from './ExpandedCodeMirrorDialog';
import {
  missingOutputFields,
  placeholderHighlight,
  placeholderTheme,
} from './prompt-template';
import type { NodeConfigEditorProps } from './types';

/** Live {{placeholder}} highlight extensions (stable identity). */
const PROMPT_EXTENSIONS: Extension[] = [placeholderHighlight, placeholderTheme];

/** The two inline-credential custom providers — picking one shows the inline
 * custom_model_config fields. Kept in sync with engine `custom_llms.py`. */
const INLINE_CUSTOM_PROVIDERS = ['OpenAI', 'Gemini'];

/** Providers whose engine class actually forwards `extra_body` to the request
 * (OpenAIModel / AzureOpenAIModel, both on the OpenAI SDK). AnthropicModel and
 * GeminiModel build their own request and ignore it, so the field is hidden for
 * them. Kept in sync with engine `custom_llms.py`. */
const EXTRA_BODY_PROVIDERS = ['openai', 'azure_openai'];

export function PromptNodeEditor({
  config,
  readOnly,
  onChange,
  outputFieldNames,
  nodeId,
  wfId,
}: NodeConfigEditorProps) {
  const { t } = useTranslation();
  // Prompt-template version-diff modal. The "History" button only renders when
  // this node lives in a workflow with >= 2 versions (otherwise there's
  // nothing to compare). The version list is cheap + shared with the modal.
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
  // Platform/built-in model names (live `llm_registry` snapshot via
  // `GET /api/v1/enums`); includes the custom providers "OpenAI"/"Gemini".
  const { options: models } = useModelOptions();
  // The tenant's SAVED LLM credentials (PUBLIC projection — names only, NO
  // secrets). The picker surface for BYO-saved-keys.
  const { data: credentials } = useLlmCredentials();

  const promptTemplate =
    typeof config.prompt_template === 'string'
      ? (config.prompt_template as string)
      : '';
  // Output fields the template does NOT reference as a quoted "name"/'name'.
  // Updates live as the template OR the declared output fields change.
  const missingFields = useMemo(
    () => missingOutputFields(promptTemplate, outputFieldNames ?? []),
    [promptTemplate, outputFieldNames],
  );
  const modelName =
    typeof config.model_name === 'string'
      ? (config.model_name as string)
      : '';
  const inference =
    config.inference_config &&
    typeof config.inference_config === 'object' &&
    !Array.isArray(config.inference_config)
      ? (config.inference_config as Record<string, unknown>)
      : {};
  const customModelConfig =
    config.custom_model_config &&
    typeof config.custom_model_config === 'object' &&
    !Array.isArray(config.custom_model_config)
      ? (config.custom_model_config as Record<string, unknown>)
      : {};

  // Saved-credential picker options: value = bare NAME, label = name (provider).
  const savedCredentials = Array.isArray(credentials) ? credentials : [];
  const savedNames = new Set(savedCredentials.map((c) => c.name));

  // Built-in / platform models, minus any name that collides with a saved
  // credential (the saved entry already renders it under its own group).
  const builtinModels = models.filter((m) => !savedNames.has(m));

  // Inline custom_model_config is only relevant for the inline providers; a
  // SAVED name resolves its secrets server-side, so hide the inline fields.
  const showInlineCustom =
    INLINE_CUSTOM_PROVIDERS.includes(modelName) && !savedNames.has(modelName);

  // Keep a stale/unknown selected value visible even if it's neither a current
  // saved name nor a built-in (e.g. a deleted credential, or no enums yet).
  const isKnown =
    savedNames.has(modelName) || builtinModels.includes(modelName);

  // `extra_body` only takes effect on the OpenAI-SDK providers, so the field is
  // shown ONLY then. The selected model's provider comes from its saved
  // credential, or — for the inline custom path — picking "OpenAI" (which maps
  // to OpenAIModel; "Gemini" does not). Built-in/unknown selections hide it.
  const selectedCredential = savedCredentials.find((c) => c.name === modelName);
  const extraBodySupported =
    (selectedCredential
      ? EXTRA_BODY_PROVIDERS.includes(selectedCredential.provider)
      : false) ||
    (showInlineCustom && modelName === 'OpenAI');

  const updateInference = (next: Record<string, unknown>) => {
    onChange({ ...config, inference_config: next });
  };
  const updateCustomModel = (next: Record<string, unknown>) => {
    onChange({ ...config, custom_model_config: next });
  };

  const customModelName =
    typeof customModelConfig.model_name === 'string'
      ? (customModelConfig.model_name as string)
      : '';
  const customApiKey =
    typeof customModelConfig.api_key === 'string'
      ? (customModelConfig.api_key as string)
      : '';
  const customApiUrl =
    typeof customModelConfig.api_url === 'string'
      ? (customModelConfig.api_url as string)
      : '';

  // Engine-aligned defaults (see CONFIG_SCHEMA in
  // engine/src/vibecanvas_engine/nodes/prompt.py). Rendered when the key is
  // absent so a fresh PromptNode satisfies the required-keys check.
  const DEFAULT_TEMPERATURE = 1.0;
  const DEFAULT_MAX_TOKENS = 512;
  const DEFAULT_TOP_K = -1;
  const DEFAULT_TOP_P = 0.9;

  const temperature =
    typeof inference.temperature === 'number'
      ? (inference.temperature as number)
      : DEFAULT_TEMPERATURE;
  const maxTokens =
    typeof inference.max_tokens === 'number'
      ? (inference.max_tokens as number)
      : DEFAULT_MAX_TOKENS;
  const topK =
    typeof inference.top_k === 'number'
      ? (inference.top_k as number)
      : DEFAULT_TOP_K;
  const topP =
    typeof inference.top_p === 'number'
      ? (inference.top_p as number)
      : DEFAULT_TOP_P;
  // extra_body: an optional JSON object string merged into the model request
  // body (OpenAI-compatible). Stored raw; soft-validated (non-blocking).
  const extraBody =
    typeof inference.extra_body === 'string' ? (inference.extra_body as string) : '';
  const extraBodyInvalid =
    extraBody.trim() !== '' &&
    (() => {
      try {
        const v = JSON.parse(extraBody);
        return typeof v !== 'object' || v === null || Array.isArray(v);
      } catch {
        return true;
      }
    })();

  // No options at all (no saved creds AND no enums fetched) → free-text so the
  // user is never blocked.
  const hasAnyOption = savedCredentials.length > 0 || builtinModels.length > 0;

  return (
    <div className="space-y-3">
      <div className="space-y-1.5">
        <div className="flex items-center justify-between">
          <Label className="text-xs font-medium">prompt_template</Label>
          <div className="flex items-center gap-1">
            {showHistoryButton && (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="h-6 gap-1 px-2 text-xs text-muted-foreground"
                onClick={() => setHistoryOpen(true)}
                data-testid="cfg-prompt-history-btn"
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
              data-testid="cfg-prompt-expand-btn"
            >
              <Maximize2 className="h-3.5 w-3.5" />
            </Button>
          </div>
        </div>
        <CodeMirrorField
          value={promptTemplate}
          onCommit={(next) =>
            onChange({ ...config, prompt_template: next })
          }
          readOnly={readOnly}
          data-testid="cfg-prompt-template"
          extensions={PROMPT_EXTENSIONS}
          minHeight="160px"
          placeholder="Prompt with {{variable}} interpolation slots…"
        />
        <ExpandedCodeMirrorDialog
          open={expandedOpen}
          onOpenChange={setExpandedOpen}
          title="prompt_template"
          meta="template"
          value={promptTemplate}
          onCommit={(next) =>
            onChange({ ...config, prompt_template: next })
          }
          readOnly={readOnly}
          extensions={PROMPT_EXTENSIONS}
          placeholder="Prompt with {{variable}} interpolation slots…"
          testId="cfg-prompt-expanded-editor"
        />
        {missingFields.length > 0 && (
          <div className="space-y-0.5" data-testid="cfg-prompt-missing-fields">
            {missingFields.map((name) => (
              <p
                key={name}
                className="text-xs leading-tight text-state-danger"
              >
                {t('prompt_node.missing_output_field', {
                  name,
                  defaultValue: 'Output field {{name}} not referenced',
                })}
              </p>
            ))}
          </div>
        )}
      </div>

      <div className="space-y-1.5">
        <Label className="text-xs font-medium">model</Label>
        {!hasAnyOption ? (
          // Graceful fallback: nothing fetched yet → free-text so the user is
          // never blocked. Shows the last-known value.
          <CommitOnBlurInput
            value={modelName}
            onCommit={(next) => onChange({ ...config, model_name: next })}
            disabled={readOnly}
            placeholder="Model name (no models configured)"
            className="h-8 text-xs"
            data-testid="cfg-prompt-model-input"
          />
        ) : (
          <Select
            value={modelName || undefined}
            onValueChange={(next) => onChange({ ...config, model_name: next })}
            disabled={readOnly}
          >
            <SelectTrigger
              className="h-8 text-xs"
              data-testid="cfg-prompt-model-select"
            >
              <SelectValue
                placeholder={t('prompt_node.select_model', 'Select a model')}
              />
            </SelectTrigger>
            <SelectContent>
              {/* Keep a stale/unknown selected model visible. */}
              {modelName && !isKnown && (
                <SelectItem value={modelName} className="text-xs">
                  {modelName} ({t('prompt_node.unavailable', 'unavailable')})
                </SelectItem>
              )}
              {savedCredentials.length > 0 && (
                <SelectGroup>
                  <SelectLabel className="text-xs">
                    {t('prompt_node.saved_api', 'Saved APIs')}
                  </SelectLabel>
                  {savedCredentials.map((c) => (
                    <SelectItem
                      key={`cred-${c.id}`}
                      value={c.name}
                      className="text-xs"
                    >
                      {c.name} ({c.provider})
                    </SelectItem>
                  ))}
                </SelectGroup>
              )}
              {builtinModels.length > 0 && (
                <SelectGroup>
                  <SelectLabel className="text-xs">
                    {t('prompt_node.builtin', 'Built-in')}
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

      {showInlineCustom && (
        <div className="space-y-1.5 rounded-md border border-dashed p-2">
          <Label className="text-xs text-muted-foreground">
            {t(
              'prompt_node.custom_config',
              'Custom credentials (entered inline)',
            )}
          </Label>
          <div className="grid grid-cols-[auto_1fr] items-center gap-x-2 gap-y-1.5">
            <Label className="text-xs text-muted-foreground">model</Label>
            <CommitOnBlurInput
              value={customModelName}
              onCommit={(next) =>
                updateCustomModel({ ...customModelConfig, model_name: next })
              }
              disabled={readOnly}
              placeholder={t('prompt_node.custom_model_placeholder', 'Model id from API configuration')}
              className="h-8 text-xs"
              data-testid="cfg-prompt-custom-model"
            />
            <Label className="text-xs text-muted-foreground">api_key</Label>
            <CommitOnBlurInput
              value={customApiKey}
              onCommit={(next) =>
                updateCustomModel({ ...customModelConfig, api_key: next })
              }
              disabled={readOnly}
              placeholder="sk-…"
              className="h-8 text-xs"
              data-testid="cfg-prompt-custom-key"
            />
            <Label className="text-xs text-muted-foreground">api_url</Label>
            <CommitOnBlurInput
              value={customApiUrl}
              onCommit={(next) =>
                updateCustomModel({ ...customModelConfig, api_url: next })
              }
              disabled={readOnly}
              placeholder="https://api.openai.com/v1 (OpenAI-compatible)"
              className="h-8 text-xs"
              data-testid="cfg-prompt-custom-url"
            />
          </div>
          <p className="text-xs leading-tight text-muted-foreground">
            {t(
              'prompt_node.saved_api_hint',
              'Tip: save reusable API keys in the API Management Center, then pick them by name above (your key stays server-side).',
            )}
          </p>
        </div>
      )}

      <div className="space-y-1.5">
        <Label className="text-xs font-medium">inference_config</Label>
        <div className="grid grid-cols-[auto_1fr] items-center gap-x-2 gap-y-1.5">
          <Label className="text-xs text-muted-foreground">temperature</Label>
          <CommitOnBlurNumber
            kind="float"
            step="0.1"
            min={0}
            max={2}
            value={temperature}
            onCommit={(next) =>
              updateInference({ ...inference, temperature: next })
            }
            disabled={readOnly}
            className="h-8 text-xs"
          />
          <Label className="text-xs text-muted-foreground">max_tokens</Label>
          <CommitOnBlurNumber
            kind="int"
            min={1}
            step={1}
            value={maxTokens}
            onCommit={(next) =>
              updateInference({ ...inference, max_tokens: next })
            }
            disabled={readOnly}
            className="h-8 text-xs"
          />
          <Label className="text-xs text-muted-foreground">top_k</Label>
          <CommitOnBlurNumber
            kind="int"
            min={-1}
            step={1}
            value={topK}
            onCommit={(next) =>
              updateInference({ ...inference, top_k: next })
            }
            disabled={readOnly}
            className="h-8 text-xs"
          />
          <Label className="text-xs text-muted-foreground">top_p</Label>
          <CommitOnBlurNumber
            kind="float"
            step="0.05"
            min={0}
            max={1}
            value={topP}
            onCommit={(next) =>
              updateInference({ ...inference, top_p: next })
            }
            disabled={readOnly}
            className="h-8 text-xs"
          />
        </div>

        {extraBodySupported && (
          <div className="space-y-1">
            <Label className="text-xs text-muted-foreground">extra_body</Label>
            <CommitOnBlurTextarea
              value={extraBody}
              onCommit={(next) =>
                updateInference({ ...inference, extra_body: next })
              }
              disabled={readOnly}
              spellCheck={false}
              className="min-h-[56px] font-mono text-xs"
              placeholder={'{"reasoning_effort": "high"}'}
              data-testid="cfg-prompt-extra-body"
            />
            <p className="text-xs leading-tight text-muted-foreground">
              {t(
                'inspector.config.prompt.extraBodyHint',
                'Optional JSON object merged into the model request body.',
              )}
            </p>
            {extraBodyInvalid && (
              <p className="text-xs leading-tight text-state-warning">
                {t(
                  'inspector.config.prompt.extraBodyInvalid',
                  'Not valid JSON — saved as-is; the model will ignore it until fixed.',
                )}
              </p>
            )}
          </div>
        )}
      </div>

      {showHistoryButton && wfId && nodeId && (
        <PromptDiffDialog
          open={historyOpen}
          onOpenChange={setHistoryOpen}
          wfId={wfId}
          nodeId={nodeId}
          currentPrompt={promptTemplate}
        />
      )}
    </div>
  );
}
