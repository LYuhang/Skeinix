/**
 * Dialog for creating or editing an LLM API credential.
 *
 * One `Dialog` serves both modes — `target == null` => create, otherwise edit
 * the given credential. Mirrors the form shape of `McpServerAddModal` (Label +
 * Input + Select via `@/components/ui/*`) but as a single panel (the form is
 * short: name / description / provider / model_name / api_url / api_key).
 *
 * Edit-mode key handling
 * ----------------------
 * The plaintext key is NEVER fetched into this form. On edit, the api_key
 * field starts empty with a "leave blank to keep current" placeholder — the
 * backend keeps the stored key when `api_key` is omitted/empty. We show a
 * "key is set" hint sourced from `useLlmCredential(id).api_key_set` so the
 * user knows a key already exists without ever seeing it.
 *
 * Provider UX
 * -----------
 * A friendly Select over the 4 CANONICAL provider ids the engine + agent
 * understand — `openai`, `azure_openai`, `anthropic`, `google_genai` — shown
 * with human labels. The stored value is the canonical id (so the agent's
 * `"{provider}:{model}"` string + the engine's provider→class map resolve
 * directly). Picking "Other" reveals a free-text input so users can still name
 * any provider — we never branch on provider downstream (BYO-LLM, one
 * provider-agnostic scheme).
 */
import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  useCreateLlmCredential,
  useLlmCredential,
  useUpdateLlmCredential,
} from '@/lib/api/queries/llm-credentials';
import type { CredentialPublic } from '@/lib/api/llm-credentials';

interface CredentialFormDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** null => create mode; a row => edit that credential. */
  target: CredentialPublic | null;
}

/**
 * The 4 canonical provider ids (engine + agent contract) offered as a friendly
 * select; anything else => "other" (free-text). `value` is the canonical id we
 * store; `labelKey`/`labelFallback` drives the human display.
 */
const KNOWN_PROVIDERS = [
  { value: 'openai', labelKey: 'credentials.provider_openai', labelFallback: 'OpenAI' },
  { value: 'azure_openai', labelKey: 'credentials.provider_azure_openai', labelFallback: 'Azure OpenAI' },
  { value: 'anthropic', labelKey: 'credentials.provider_anthropic', labelFallback: 'Anthropic' },
  { value: 'google_genai', labelKey: 'credentials.provider_google_genai', labelFallback: 'Google Gemini' },
] as const;
const KNOWN_PROVIDER_IDS = KNOWN_PROVIDERS.map((p) => p.value) as readonly string[];
const OTHER = '__other__';

interface FormState {
  name: string;
  description: string;
  /** The select value: a known provider, or OTHER. */
  providerChoice: string;
  /** Free-text provider when providerChoice === OTHER. */
  providerCustom: string;
  model_name: string;
  model_context_tokens: string;
  api_url: string;
  proxy: string;
  api_key: string;
}

function emptyForm(): FormState {
  return {
    name: '',
    description: '',
    providerChoice: 'openai',
    providerCustom: '',
    model_name: '',
    model_context_tokens: '',
    api_url: '',
    proxy: '',
    api_key: '',
  };
}

export function CredentialFormDialog({
  open,
  onOpenChange,
  target,
}: CredentialFormDialogProps) {
  const { t } = useTranslation();
  const isEdit = target != null;

  // Owner view for edit mode — gives model_name / api_url / api_key_set.
  // Disabled (id undefined) in create mode.
  const ownerQuery = useLlmCredential(isEdit ? target.id : undefined);

  const createMutation = useCreateLlmCredential();
  const updateMutation = useUpdateLlmCredential();
  const pending = createMutation.isPending || updateMutation.isPending;

  const [form, setForm] = useState<FormState>(emptyForm);

  // (Re)seed the form whenever the dialog opens or the owner view arrives.
  // Create mode resets to blanks; edit mode hydrates from the owner view
  // (but NEVER the key — that field always starts empty).
  useEffect(() => {
    if (!open) return;
    const o = ownerQuery.data;
    let active = true;
    queueMicrotask(() => {
      if (!active) return;
      if (!isEdit) {
        setForm(emptyForm());
        return;
      }
      const provider = o?.provider ?? target.provider;
      const known = KNOWN_PROVIDER_IDS.includes(provider);
      setForm({
        name: o?.name ?? target.name,
        description: o?.description ?? target.description ?? '',
        providerChoice: known ? provider : OTHER,
        providerCustom: known ? '' : provider,
        model_name: o?.model_name ?? '',
        model_context_tokens: o?.model_context_tokens ? String(o.model_context_tokens) : '',
        api_url: o?.api_url ?? '',
        proxy: o?.proxy ?? '',
        api_key: '',
      });
    });
    return () => {
      active = false;
    };
  }, [open, isEdit, ownerQuery.data, target]);

  const effectiveProvider =
    form.providerChoice === OTHER
      ? form.providerCustom.trim()
      : form.providerChoice;

  const keySet = ownerQuery.data?.api_key_set ?? false;

  const canSubmit = useMemo(() => {
    if (!form.name.trim()) return false;
    if (!effectiveProvider) return false;
    if (!form.model_name.trim()) return false;
    if (form.model_context_tokens.trim()) {
      const n = Number(form.model_context_tokens.trim());
      if (!Number.isFinite(n) || n <= 0 || !Number.isInteger(n)) return false;
    }
    // Create requires a key; edit may leave it blank to keep the existing one.
    if (!isEdit && !form.api_key) return false;
    return true;
  }, [form, effectiveProvider, isEdit]);

  const handleSubmit = async () => {
    const description = form.description.trim() || null;
    const model_context_tokens = form.model_context_tokens.trim()
      ? Number(form.model_context_tokens.trim())
      : null;
    const api_url = form.api_url.trim() || null;
    const proxy = form.proxy.trim() || null;
    try {
      if (isEdit) {
        await updateMutation.mutateAsync({
          id: target.id,
          body: {
            name: form.name.trim(),
            description,
            provider: effectiveProvider,
            model_name: form.model_name.trim(),
            model_context_tokens,
            api_url,
            proxy,
            // Omit empty key => backend keeps the existing one.
            ...(form.api_key ? { api_key: form.api_key } : {}),
          },
        });
        toast.success(t('credentials.updated', 'Credential updated'));
      } else {
        await createMutation.mutateAsync({
          name: form.name.trim(),
          description,
          provider: effectiveProvider,
          model_name: form.model_name.trim(),
          model_context_tokens,
          api_url,
          ...(proxy ? { proxy } : {}),
          api_key: form.api_key,
        });
        toast.success(t('credentials.created', 'Credential saved'));
      }
      onOpenChange(false);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>
            {isEdit
              ? t('credentials.edit_title', 'Edit credential')
              : t('credentials.add_title', 'Add credential')}
          </DialogTitle>
          <DialogDescription>
            {t(
              'credentials.form_desc',
              'Store an LLM API key for this workspace. Keys are encrypted at rest and cannot be read back after saving.',
            )}
          </DialogDescription>
        </DialogHeader>

        <div className="flex flex-col gap-4">
          <div className="flex flex-col gap-1">
            <Label htmlFor="cred-name">{t('credentials.name', 'Name')}</Label>
            <Input
              id="cred-name"
              data-testid="cred-name"
              required
              value={form.name}
              placeholder={t(
                'credentials.name_ph',
                'e.g. My OpenAI key',
              )}
              onChange={(e) =>
                setForm((f) => ({ ...f, name: e.target.value }))
              }
            />
          </div>

          <div className="flex flex-col gap-1">
            <Label htmlFor="cred-desc">
              {t('credentials.description', 'Description')}
            </Label>
            <Textarea
              id="cred-desc"
              data-testid="cred-desc"
              rows={2}
              value={form.description}
              placeholder={t(
                'credentials.description_ph',
                'Optional note to help you remember what this is for',
              )}
              onChange={(e) =>
                setForm((f) => ({ ...f, description: e.target.value }))
              }
            />
          </div>

          <div className="flex flex-col gap-1">
            <Label htmlFor="cred-provider">
              {t('credentials.provider', 'Provider')}
            </Label>
            <Select
              value={form.providerChoice}
              onValueChange={(v) =>
                setForm((f) => ({ ...f, providerChoice: v }))
              }
            >
              <SelectTrigger id="cred-provider" data-testid="cred-provider">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {KNOWN_PROVIDERS.map((p) => (
                  <SelectItem key={p.value} value={p.value}>
                    {t(p.labelKey, p.labelFallback)}
                  </SelectItem>
                ))}
                <SelectItem value={OTHER}>
                  {t('credentials.provider_other', 'Other / custom')}
                </SelectItem>
              </SelectContent>
            </Select>
            {form.providerChoice === OTHER && (
              <Input
                className="mt-1"
                data-testid="cred-provider-custom"
                value={form.providerCustom}
                placeholder={t(
                  'credentials.provider_custom_ph',
                  'Provider name',
                )}
                onChange={(e) =>
                  setForm((f) => ({ ...f, providerCustom: e.target.value }))
                }
              />
            )}
          </div>

          <div className="flex flex-col gap-1">
            <Label htmlFor="cred-model">
              {t('credentials.model_name', 'Model')}
            </Label>
            <Input
              id="cred-model"
              data-testid="cred-model"
              required
              value={form.model_name}
              placeholder={t('credentials.model_name_ph', 'e.g. gpt-4o')}
              onChange={(e) =>
                setForm((f) => ({ ...f, model_name: e.target.value }))
              }
            />
          </div>

          <div className="flex flex-col gap-1">
            <Label htmlFor="cred-context">
              {t('credentials.model_context_tokens', 'Context window')}
            </Label>
            <Input
              id="cred-context"
              data-testid="cred-context"
              type="number"
              min={1}
              step={1}
              inputMode="numeric"
              value={form.model_context_tokens}
              placeholder={t(
                'credentials.model_context_tokens_ph',
                'Optional — e.g. 128000',
              )}
              onChange={(e) =>
                setForm((f) => ({
                  ...f,
                  model_context_tokens: e.target.value,
                }))
              }
            />
            <span className="text-xs text-muted-foreground">
              {t(
                'credentials.model_context_tokens_helper',
                'Optional — used to derive memory compression thresholds for this model.',
              )}
            </span>
          </div>

          <div className="flex flex-col gap-1">
            <Label htmlFor="cred-url">
              {t('credentials.api_url', 'API base URL')}
            </Label>
            <Input
              id="cred-url"
              data-testid="cred-url"
              value={form.api_url}
              placeholder={t(
                'credentials.api_url_ph',
                'Optional — leave blank for the provider default',
              )}
              onChange={(e) =>
                setForm((f) => ({ ...f, api_url: e.target.value }))
              }
            />
          </div>

          <div className="flex flex-col gap-1">
            <Label htmlFor="cred-proxy">
              {t('credentials.proxy', 'Proxy')}
            </Label>
            <Input
              id="cred-proxy"
              data-testid="cred-proxy"
              value={form.proxy}
              placeholder={t(
                'credentials.proxy_ph',
                'e.g. http://host:port',
              )}
              onChange={(e) =>
                setForm((f) => ({ ...f, proxy: e.target.value }))
              }
            />
            <span className="text-xs text-muted-foreground">
              {t(
                'credentials.proxy_helper',
                'Optional — route this provider’s requests through an HTTP/HTTPS proxy, e.g. http://host:port',
              )}
            </span>
          </div>

          <div className="flex flex-col gap-1">
            <Label htmlFor="cred-key">
              {t('credentials.api_key', 'API key')}
            </Label>
            <Input
              id="cred-key"
              data-testid="cred-key"
              type="password"
              autoComplete="off"
              value={form.api_key}
              placeholder={
                isEdit
                  ? t(
                      'credentials.key_kept_hint',
                      'Leave blank to keep the current key',
                    )
                  : t('credentials.api_key_ph', 'Paste your secret key')
              }
              onChange={(e) =>
                setForm((f) => ({ ...f, api_key: e.target.value }))
              }
            />
            {isEdit && keySet && (
              <span className="text-xs text-muted-foreground">
                {t('credentials.key_set', 'A key is currently set.')}
              </span>
            )}
          </div>
        </div>

        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={pending}
          >
            {t('credentials.cancel', 'Cancel')}
          </Button>
          <Button
            type="button"
            data-testid="cred-save"
            onClick={() => void handleSubmit()}
            disabled={!canSubmit || pending}
          >
            {pending
              ? t('credentials.saving', 'Saving…')
              : t('credentials.save', 'Save')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
