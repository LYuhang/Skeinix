/** Workflow execution settings. */
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
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
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Textarea } from '@/components/ui/textarea';
import { useWorkflowEditStore } from '@/stores/workflow-edit';

/** Engine defaults — shown as input placeholders; empty/0 means "use default". */
const TIMEOUT_DEFAULTS = { workflow: 3600, code: 60, http: 30 } as const;
type TimeoutKind = keyof typeof TIMEOUT_DEFAULTS;
const TIMEOUT_KINDS: TimeoutKind[] = ['workflow', 'code', 'http'];

interface Settings {
  /** Incremental packages prepared on top of the platform sandbox image. */
  code_requirements?: string;
  /** Retired user-controlled package source; package sources are ops-managed. */
  code_index_url?: string;
  /** Retired curated-library representation; requirements is the source of truth. */
  code_libraries?: string[];
  timeouts?: Partial<Record<TimeoutKind, number>>;
  /** Extra hosts the workflow's sandbox may reach in production. */
  egress?: { allowed_hosts: string[] };
  /** Sibling sub-keys this modal does not own but must preserve on save. */
  [key: string]: unknown;
}

/** Parse a one-host-per-line textarea into a trimmed, deduped, non-empty list. */
function parseHosts(text: string): string[] {
  const out: string[] = [];
  const seen = new Set<string>();
  for (const line of text.split('\n')) {
    const host = line.trim();
    if (host === '' || seen.has(host)) continue;
    seen.add(host);
    out.push(host);
  }
  return out;
}

function readSettings(draft: Record<string, unknown> | null): Settings {
  const meta = draft?.__meta__ as Record<string, unknown> | undefined;
  const settings = meta?.settings as Settings | undefined;
  return settings ?? {};
}

function timeoutDraft(settings: Settings): Record<TimeoutKind, string> {
  return {
    workflow: settings.timeouts?.workflow != null ? String(settings.timeouts.workflow) : '',
    code: settings.timeouts?.code != null ? String(settings.timeouts.code) : '',
    http: settings.timeouts?.http != null ? String(settings.timeouts.http) : '',
  };
}

export interface WorkflowSettingsModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function WorkflowSettingsModal({ open, onOpenChange }: WorkflowSettingsModalProps) {
  const { t } = useTranslation();

  const applyEdit = useWorkflowEditStore((s) => s.applyEdit);

  // Local draft state — seeded from the live draft's settings, reset on open.
  // Keep timeout inputs as strings so an empty field round-trips to "default".
  const [timeouts, setTimeouts] = useState<Record<TimeoutKind, string>>(() =>
    timeoutDraft(readSettings(useWorkflowEditStore.getState().draft)),
  );
  // Egress allowed-hosts as one-host-per-line text.
  const [egressHosts, setEgressHosts] = useState(() =>
    (readSettings(useWorkflowEditStore.getState().draft).egress?.allowed_hosts ?? []).join('\n'),
  );
  const [requirements, setRequirements] = useState(
    () => readSettings(useWorkflowEditStore.getState().draft).code_requirements ?? '',
  );
  useEffect(() => {
    if (!open) return;
    const current = readSettings(useWorkflowEditStore.getState().draft);
    let active = true;
    queueMicrotask(() => {
      if (!active) return;
      setTimeouts(timeoutDraft(current));
      setEgressHosts((current.egress?.allowed_hosts ?? []).join('\n'));
      setRequirements(current.code_requirements ?? '');
    });
    return () => {
      active = false;
    };
  }, [open]);

  const setTimeout = (kind: TimeoutKind, value: string) => {
    setTimeouts((prev) => ({ ...prev, [kind]: value }));
  };

  const onSave = () => {
    // Build the new settings object. Omit empty/0 timeouts (absent = default)
    // and omit the timeouts object entirely when none are set.
    const nextTimeouts: Partial<Record<TimeoutKind, number>> = {};
    for (const kind of TIMEOUT_KINDS) {
      const raw = timeouts[kind].trim();
      if (raw === '') continue;
      const n = Number(raw);
      if (Number.isFinite(n) && n > 0) nextTimeouts[kind] = n;
    }
    // Preserve sibling sub-keys this modal does not own, then overlay only the
    // slices it owns. code_requirements is an incremental layer on top of the
    // platform base environment; package-source selection remains ops-managed.
    const existing = readSettings(useWorkflowEditStore.getState().draft);
    const settings: Settings = { ...existing };
    if (Object.keys(nextTimeouts).length > 0) settings.timeouts = nextTimeouts;
    else delete settings.timeouts;
    const hosts = parseHosts(egressHosts);
    if (hosts.length > 0) settings.egress = { allowed_hosts: hosts };
    else delete settings.egress;
    const nextRequirements = requirements.trim();
    if (nextRequirements) settings.code_requirements = nextRequirements;
    else delete settings.code_requirements;
    delete settings.code_index_url;
    delete settings.code_libraries;
    delete settings.agent_tools;

    applyEdit((wf) => {
      wf.__meta__ = { ...((wf.__meta__ as Record<string, unknown>) ?? {}), settings };
      return wf;
    });
    onOpenChange(false);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent data-testid="workflow-settings-modal" className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>{t('settings.workflow.title', 'Workflow settings')}</DialogTitle>
          <DialogDescription>
            {t(
              'settings.workflow.subtitle',
              'Per-workflow execution settings. Committed on Save.',
            )}
          </DialogDescription>
        </DialogHeader>

        <Tabs
          defaultValue="timeouts"
          orientation="vertical"
          className="flex max-h-[min(65dvh,36rem)] flex-col items-stretch gap-4 sm:flex-row sm:items-start"
        >
          <TabsList
            aria-orientation="vertical"
            className="flex h-auto w-full shrink-0 flex-row items-stretch gap-1 overflow-x-auto bg-transparent p-0 sm:w-32 sm:flex-col"
          >
            <TabsTrigger
              value="timeouts"
              className="min-w-fit flex-1 justify-start sm:w-full"
              data-testid="settings-tab-timeouts"
            >
              {t('settings.workflow.tab.timeouts', 'Timeouts')}
            </TabsTrigger>
            <TabsTrigger
              value="code"
              className="min-w-fit flex-1 justify-start sm:w-full"
              data-testid="settings-tab-code"
            >
              {t('settings.workflow.tab.code', 'Python')}
            </TabsTrigger>
            <TabsTrigger
              value="egress"
              className="min-w-fit flex-1 justify-start sm:w-full"
              data-testid="settings-tab-egress"
            >
              {t('settings.workflow.tab.egress', 'Network')}
            </TabsTrigger>
          </TabsList>

          <div className="min-h-0 min-w-0 flex-1 overflow-y-auto">
            {/* ----- Timeouts ----- */}
            <TabsContent
              forceMount
              value="timeouts"
              className="mt-0 data-[state=inactive]:hidden"
            >
              <section className="space-y-2">
                <h3 className="text-sm font-medium">
                  {t('settings.workflow.timeoutsTitle', 'Timeouts (seconds)')}
                </h3>
                <p className="text-xs text-muted-foreground">
                  {t('settings.workflow.timeoutsHint', 'Leave empty (or 0) to use the default.')}
                </p>
                <div className="grid gap-3 sm:grid-cols-3">
                  {TIMEOUT_KINDS.map((kind) => (
                    <div key={kind} className="space-y-1">
                      <Label htmlFor={`settings-timeout-${kind}`} className="text-xs">
                        {t(`settings.workflow.timeout.${kind}`, kind)}
                      </Label>
                      <Input
                        id={`settings-timeout-${kind}`}
                        data-testid={`settings-timeout-${kind}`}
                        type="number"
                        min={0}
                        placeholder={String(TIMEOUT_DEFAULTS[kind])}
                        value={timeouts[kind]}
                        onChange={(e) => setTimeout(kind, e.target.value)}
                      />
                    </div>
                  ))}
                </div>
              </section>
            </TabsContent>

            {/* ----- CodeNode Python dependencies ----- */}
            <TabsContent
              forceMount
              value="code"
              className="mt-0 data-[state=inactive]:hidden"
            >
              <section className="space-y-3">
                <div className="space-y-1">
                  <h3 className="text-sm font-medium">
                    {t('settings.workflow.python.title', 'Python dependencies')}
                  </h3>
                  <p className="text-xs text-muted-foreground">
                    {t(
                      'settings.workflow.python.baseHint',
                      'The sandbox includes the platform Python environment. Declare only additional packages required by this workflow.',
                    )}
                  </p>
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="settings-code-requirements" className="text-xs">
                    {t('settings.workflow.python.requirements', 'Additional packages')}
                  </Label>
                  <Textarea
                    id="settings-code-requirements"
                    data-testid="settings-code-requirements"
                    rows={9}
                    spellCheck={false}
                    className="font-mono text-xs"
                    placeholder={'pandas==2.2.0\nopenpyxl==3.1.5'}
                    value={requirements}
                    onChange={(event) => setRequirements(event.target.value)}
                  />
                  <p className="text-xs text-muted-foreground">
                    {t(
                      'settings.workflow.requirementsHint',
                      'Declare packages in requirements.txt format, one per line. Pin versions for reproducible runs.',
                    )}
                  </p>
                </div>
                <div
                  className="rounded-md border border-edge-subtle bg-muted/35 px-3 py-2 text-xs text-muted-foreground"
                  data-testid="settings-code-env-status"
                >
                  {t(
                    'settings.workflow.env.executionHint',
                    'Saving only records this list. Packages are installed when a new sandbox is initialized to execute a node or this workflow.',
                  )}
                </div>
              </section>
            </TabsContent>

            {/* ----- Network egress allowlist ----- */}
            <TabsContent
              forceMount
              value="egress"
              className="mt-0 data-[state=inactive]:hidden"
            >
              <section className="space-y-2">
                <h3 className="text-sm font-medium">
                  {t('settings.workflow.egress.title', 'Network access')}
                </h3>
                <p className="text-xs text-muted-foreground">
                  {t(
                    'settings.workflow.egress.hint',
                    'Extra hosts your workflow may reach, one per line (e.g. api.example.com).',
                  )}
                </p>
                <Textarea
                  data-testid="settings-egress-hosts"
                  rows={6}
                  spellCheck={false}
                  className="font-mono text-xs"
                  placeholder={'api.example.com\ndata.example.org'}
                  value={egressHosts}
                  onChange={(e) => setEgressHosts(e.target.value)}
                />
                <p className="text-xs text-muted-foreground">
                  {t(
                    'settings.workflow.egress.notice',
                    "In production, this workflow's sandbox can only reach the hosts you list here plus its own AI model endpoints. Everything else is blocked. In development, network access is unrestricted.",
                  )}
                </p>
              </section>
            </TabsContent>
          </div>
        </Tabs>

        <DialogFooter>
          <Button
            variant="outline"
            data-action="settings-cancel"
            onClick={() => onOpenChange(false)}
          >
            {t('cancel', 'Cancel')}
          </Button>
          <Button data-action="settings-save" onClick={onSave}>
            {t('save', 'Save')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
