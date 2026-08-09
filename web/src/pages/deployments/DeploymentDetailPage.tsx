import { useMemo, useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import {
  Code2,
  Copy,
  KeyRound,
  Play,
  RefreshCw,
  Rocket,
  Share2,
  ShieldCheck,
} from 'lucide-react';

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
import { Switch } from '@/components/ui/switch';
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from '@/components/ui/tabs';
import { Textarea } from '@/components/ui/textarea';
import {
  getDeployment,
  getHistory,
  getMetrics,
  patchDeployment,
  rotateKey,
  testInvoke,
  type Deployment,
  type HistoryItem,
  type MetricsPoint,
} from '@/lib/api/deployments';
import { useFormatDateTime } from '@/lib/timezone';
import { EntityDetailShell } from '@/components/layout/entity-detail-shell';
import { ResourceShareDialog } from '@/components/modals/ResourceShareDialog';
import { CopyButton } from '@/components/ui/copy-button';
import { StatusBadge } from '@/components/ui/status';
import { formatNumber } from '@/lib/format/number';
import { ActionableError } from '@/components/presentation/ActionableError';
import { resolveApiUrl } from '@/lib/base-path';
import { OneTimeSecretField } from '@/pages/deployments/OneTimeSecretField';

type TabKey = 'overview' | 'config' | 'code' | 'runs' | 'monitoring' | 'test' | 'security';
type CodeLanguage = 'curl' | 'python' | 'javascript';

function endpointFor(dep: Deployment): string {
  return dep.trigger_type === 'webhook'
    ? `/api/v1/deployments/${dep.slug}/webhook`
    : `/api/v1/deployments/${dep.slug}/invoke`;
}

function last24HoursRange() {
  const to = new Date();
  const from = new Date(to.getTime() - 24 * 3600 * 1000);
  return { from: from.toISOString(), to: to.toISOString() };
}

function triggerLabel(dep: Deployment): string {
  if (dep.trigger_type === 'api') return 'API';
  if (dep.trigger_type === 'webhook') return 'Webhook';
  return dep.trigger_type;
}

function copy(value: string, ok: string, fail: string) {
  if (!navigator.clipboard?.writeText) {
    toast.error(fail);
    return;
  }
  navigator.clipboard.writeText(value).then(
    () => toast.success(ok),
    () => toast.error(fail),
  );
}

function MetricCard({
  label,
  value,
  hint,
}: {
  label: string;
  value: string | number;
  hint?: string;
}) {
  return (
    <div className="border-l border-edge-subtle px-4 py-3 first:border-l-0">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="mt-2 text-2xl font-semibold tabular-nums">{value}</div>
      {hint && <div className="mt-1 text-xs leading-4 text-muted-foreground">{hint}</div>}
    </div>
  );
}

function OverviewTab({
  dep,
  latestMetric,
  onCopyEndpoint,
  onTest,
}: {
  dep: Deployment;
  latestMetric: MetricsPoint | null;
  onCopyEndpoint: () => void;
  onTest?: () => void;
}) {
  const { t } = useTranslation();
  const formatTime = useFormatDateTime();
  const errorRate =
    latestMetric && latestMetric.calls > 0
      ? `${Math.round((latestMetric.errors / latestMetric.calls) * 1000) / 10}%`
      : '0%';

  return (
    <div className="space-y-5">
      <section className="grid gap-3 md:grid-cols-4">
        <MetricCard
          label={t('deployments.detail.status', 'Status')}
          value={dep.enabled
            ? t('deployments.status.active', 'Active')
            : t('deployments.status.disabled', 'Disabled')}
          hint={t('deployments.detail.statusHint', 'Whether this deployment is currently serving requests.')}
        />
        <MetricCard
          label={t('deployments.detail.calls', 'Calls')}
          value={dep.invoke_count ?? 0}
          hint={t('deployments.detail.totalCalls', 'Total recorded API, webhook, and test invocations.')}
        />
        <MetricCard
          label={t('deployments.detail.errorRate', 'Error rate')}
          value={errorRate}
          hint={t('deployments.detail.errorRateHint', 'Errors divided by calls in the latest metrics bucket.')}
        />
        <MetricCard
          label="P95"
          value={latestMetric?.latency_p95 == null ? '-' : `${latestMetric.latency_p95.toFixed(0)} ms`}
          hint={t('deployments.detail.p95Hint', '95th percentile latency in the latest metrics bucket.')}
        />
      </section>

      <section className="border-y border-edge-subtle py-4">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0">
            <h2 className="text-sm font-semibold">
              {t('deployments.detail.endpoint', 'Endpoint')}
            </h2>
            <code className="mt-2 block max-w-full select-text truncate rounded-md border bg-muted/40 px-3 py-2 font-mono text-xs">
              {endpointFor(dep)}
            </code>
            <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
              <span>{triggerLabel(dep)}</span>
              <span>{t('deployments.detail.versionPolicy', 'Version')}: {dep.version_pin}</span>
              <span>{t('deployments.detail.lastInvoked', 'Last invoked')}: {formatTime(dep.last_invoked_at)}</span>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <Button variant="outline" size="sm" onClick={onCopyEndpoint}>
              <Copy className="mr-2 h-4 w-4" />
              {t('deployments.actions.copyEndpoint', 'Copy endpoint')}
            </Button>
            {onTest ? (
              <Button size="sm" onClick={onTest}>
                <Play className="mr-2 h-4 w-4" />
                {t('deployments.detail.tabs.test', 'Test')}
              </Button>
            ) : null}
          </div>
        </div>
      </section>
    </div>
  );
}

function ConfigTab({ dep }: { dep: Deployment }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [name, setName] = useState(dep.name);
  const [rateQps, setRateQps] = useState<number>(dep.rate_limit_qps);
  const [enabled, setEnabled] = useState(dep.enabled);

  const dirty = name !== dep.name || rateQps !== dep.rate_limit_qps || enabled !== dep.enabled;
  const patchMutation = useMutation({
    mutationFn: () =>
      patchDeployment(dep.id, {
        name,
        rate_limit_qps: rateQps,
        enabled,
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['deployment', dep.id] });
      void qc.invalidateQueries({ queryKey: ['deployments'] });
      toast.success(t('deployments.detail.saved', 'Saved'));
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : String(e)),
  });

  return (
    <div className="space-y-5">
      <section className="border-y border-edge-subtle py-4">
        <h2 className="text-sm font-semibold">{t('deployments.detail.basic', 'Basic')}</h2>
        <div className="mt-4 grid gap-4 md:grid-cols-2">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="dep-name">{t('deployments.create.fields.name', 'Name')}</Label>
            <Input id="dep-name" value={name} onChange={(event) => setName(event.target.value)} />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label>{t('deployments.create.fields.slug', 'Slug')}</Label>
            <Input value={dep.slug} disabled />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label>{t('deployments.create.fields.wfId', 'Workflow ID')}</Label>
            <Input value={dep.wf_id} disabled className="font-mono text-xs" />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label>{t('deployments.create.fields.triggerType', 'Trigger type')}</Label>
            <Input value={triggerLabel(dep)} disabled />
          </div>
        </div>
      </section>

      <section className="border-y border-edge-subtle py-4">
        <h2 className="text-sm font-semibold">{t('deployments.detail.runtime', 'Runtime limits')}</h2>
        <div className="mt-4 grid gap-4 md:grid-cols-2">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="dep-qps">{t('deployments.create.fields.rateLimitQps', 'Rate limit (QPS)')}</Label>
            <Input
              id="dep-qps"
              type="number"
              min={0}
              value={rateQps}
              onChange={(event) => setRateQps(Number(event.target.value) || 0)}
            />
          </div>
          <label className="flex items-center gap-2 pt-6">
            <Switch checked={enabled} onCheckedChange={setEnabled} />
            <span className="text-sm">{t('deployments.col.enabled', 'Enabled')}</span>
          </label>
        </div>
        <p className="mt-3 text-xs text-muted-foreground">
          {t(
            'deployments.detail.capacityNote',
            'Capacity controls such as instances, workers, memory, and autoscaling are reserved for the runtime backend work. This page only exposes limits already supported by the API.',
          )}
        </p>
      </section>

      <div className="flex justify-end">
        <Button onClick={() => patchMutation.mutate()} disabled={!dirty || patchMutation.isPending}>
          {t('deployments.detail.save', 'Save changes')}
        </Button>
      </div>
    </div>
  );
}

function deploymentCodeExamples(dep: Deployment): Record<CodeLanguage, string> {
  const endpoint = resolveApiUrl(endpointFor(dep));
  if (dep.trigger_type === 'webhook') {
    return {
      curl: [
        "payload='{\"input\":\"hello\"}'",
        'timestamp="$(date +%s)"',
        'signature="$(printf \'%s\' "${timestamp}.${payload}" | openssl dgst -sha256 -hmac "${SKEINIX_WEBHOOK_SECRET}" | awk \'{print $2}\')"',
        '',
        `curl --request POST '${endpoint}' \\`,
        "  --header 'Content-Type: application/json' \\",
        '  --header "X-Vibecanvas-Timestamp: ${timestamp}" \\',
        '  --header "X-Vibecanvas-Signature: sha256=${signature}" \\',
        '  --data "${payload}"',
      ].join('\n'),
      python: [
        'import hashlib',
        'import hmac',
        'import json',
        'import os',
        'import time',
        '',
        'import requests',
        '',
        `url = ${JSON.stringify(endpoint)}`,
        'payload = json.dumps({"input": "hello"}, separators=(",", ":"))',
        'timestamp = str(int(time.time()))',
        'secret = os.environ["SKEINIX_WEBHOOK_SECRET"].encode()',
        'signature = hmac.new(',
        '    secret, f"{timestamp}.{payload}".encode(), hashlib.sha256',
        ').hexdigest()',
        'response = requests.post(',
        '    url,',
        '    data=payload,',
        '    headers={',
        '        "Content-Type": "application/json",',
        '        "X-Vibecanvas-Timestamp": timestamp,',
        '        "X-Vibecanvas-Signature": f"sha256={signature}",',
        '    },',
        '    timeout=60,',
        ')',
        'response.raise_for_status()',
        'print(response.json())',
      ].join('\n'),
      javascript: [
        "import { createHmac } from 'node:crypto';",
        '',
        `const url = ${JSON.stringify(endpoint)};`,
        "const payload = JSON.stringify({ input: 'hello' });",
        'const timestamp = Math.floor(Date.now() / 1000).toString();',
        "const secret = process.env.SKEINIX_WEBHOOK_SECRET;",
        "if (!secret) throw new Error('SKEINIX_WEBHOOK_SECRET is required');",
        "const signature = createHmac('sha256', secret)",
        "  .update(`${timestamp}.${payload}`)",
        "  .digest('hex');",
        'const response = await fetch(url, {',
        "  method: 'POST',",
        '  headers: {',
        "    'Content-Type': 'application/json',",
        "    'X-Vibecanvas-Timestamp': timestamp,",
        "    'X-Vibecanvas-Signature': `sha256=${signature}` ,",
        '  },',
        '  body: payload,',
        '});',
        'if (!response.ok) throw new Error(`${response.status} ${await response.text()}`);',
        'console.log(await response.json());',
      ].join('\n'),
    };
  }

  return {
    curl: [
      `curl --request POST '${endpoint}' \\`,
      "  --header 'Content-Type: application/json' \\",
      "  --header 'Authorization: Bearer ${SKEINIX_API_KEY}' \\",
      "  --data '{\"input\":\"hello\"}'",
    ].join('\n'),
    python: [
      'import os',
      'import requests',
      '',
      `response = requests.post(${JSON.stringify(endpoint)},`,
      '    headers={"Authorization": f"Bearer {os.environ[\'SKEINIX_API_KEY\']}"},',
      '    json={"input": "hello"},',
      '    timeout=60,',
      ')',
      'response.raise_for_status()',
      'print(response.json())',
    ].join('\n'),
    javascript: [
      `const response = await fetch(${JSON.stringify(endpoint)}, {`,
      "  method: 'POST',",
      '  headers: {',
      "    'Content-Type': 'application/json',",
      "    Authorization: `Bearer ${process.env.SKEINIX_API_KEY}` ,",
      '  },',
      "  body: JSON.stringify({ input: 'hello' }),",
      '});',
      'if (!response.ok) throw new Error(`${response.status} ${await response.text()}`);',
      'console.log(await response.json());',
    ].join('\n'),
  };
}

function CodeExamplesTab({ dep }: { dep: Deployment }) {
  const { t } = useTranslation();
  const [language, setLanguage] = useState<CodeLanguage>('curl');
  const examples = useMemo(() => deploymentCodeExamples(dep), [dep]);
  const code = examples[language];
  return (
    <section className="border-y border-edge-subtle py-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <Code2 className="size-4 text-focus" />
            <h2 className="text-sm font-semibold">
              {t('deployments.code.title', 'Call this deployment')}
            </h2>
          </div>
          <p className="mt-1 text-xs text-muted-foreground">
            {t(
              'deployments.code.placeholderHint',
              'Examples use environment-variable placeholders and never include your secret value.',
            )}
          </p>
        </div>
        <CopyButton
          value={code}
          label={t('deployments.code.copy', 'Copy code example')}
          copiedLabel={t('deployments.actions.copied', 'Copied')}
        />
      </div>
      <Tabs value={language} onValueChange={(value) => setLanguage(value as CodeLanguage)} className="mt-4">
        <TabsList aria-label={t('deployments.code.language', 'Code language')}>
          <TabsTrigger value="curl">cURL</TabsTrigger>
          <TabsTrigger value="python">Python</TabsTrigger>
          <TabsTrigger value="javascript">JavaScript</TabsTrigger>
        </TabsList>
        {(['curl', 'python', 'javascript'] as const).map((value) => (
          <TabsContent key={value} value={value} className="mt-3">
            <pre className="app-scrollbar max-h-[32rem] overflow-auto rounded-lg bg-surface-sunken p-4 font-mono text-xs leading-5 text-foreground" data-testid={`deployment-code-${value}`}>
              <code>{examples[value]}</code>
            </pre>
          </TabsContent>
        ))}
      </Tabs>
    </section>
  );
}

function RunsTab({ depId, active }: { depId: string; active: boolean }) {
  const { t } = useTranslation();
  const formatTime = useFormatDateTime();
  const query = useQuery({
    queryKey: ['deployment-history', depId],
    queryFn: () => getHistory(depId, { limit: 50 }),
    enabled: active,
    refetchOnWindowFocus: false,
    refetchInterval: active ? 10_000 : false,
  });
  const rows: HistoryItem[] = query.data?.items ?? [];

  if (!active) return null;
  if (query.isLoading) {
    return <div className="empty-state">{t('tasks.loading', 'Loading...')}</div>;
  }
  if (query.isError) {
    return (
      <div className="rounded-md border border-destructive/40 bg-destructive/10 p-4 text-sm text-destructive">
        {t('deployments.detail.historyError', 'Failed to load runs.')}
      </div>
    );
  }
  if (rows.length === 0) {
    return (
      <div className="empty-state">
        <div className="empty-state-title">{t('deployments.detail.noHistory', 'No runs yet.')}</div>
        <div className="empty-state-copy">
          {t('deployments.detail.noHistoryHint', 'API, webhook, and test invocations will appear here after the first request.')}
        </div>
      </div>
    );
  }

  return (
    <div className="overflow-x-auto border-y border-edge-subtle">
      <table className="w-full text-sm">
        <thead className="border-b bg-surface-sunken text-left text-xs font-medium text-muted-foreground">
          <tr>
            <th className="px-4 py-3 font-medium">{t('deployments.detail.requestId', 'Request id')}</th>
            <th className="px-4 py-3 font-medium">{t('deployments.detail.source', 'Source')}</th>
            <th className="px-4 py-3 font-medium">{t('tasks.col.status', 'Status')}</th>
            <th className="px-4 py-3 font-medium">{t('tasks.col.submitted', 'Started')}</th>
            <th className="px-4 py-3 font-medium">{t('deployments.detail.finished', 'Finished')}</th>
            <th className="px-4 py-3 text-right font-medium">{t('deployments.detail.latency', 'Latency')}</th>
            <th className="px-4 py-3 font-medium">{t('deployments.detail.errorCol', 'Error')}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.id} className="border-b last:border-b-0">
              <td className="px-4 py-3 font-mono text-xs">{row.id}</td>
              <td className="px-4 py-3 text-muted-foreground">{row.source ?? '-'}</td>
              <td className="px-4 py-3">{row.status}</td>
              <td className="px-4 py-3 text-muted-foreground">{formatTime(row.started_at ?? row.submitted_at)}</td>
              <td className="px-4 py-3 text-muted-foreground">{formatTime(row.finished_at)}</td>
              <td className="px-4 py-3 text-right tabular-nums text-muted-foreground">
                {row.latency_ms == null ? '-' : `${row.latency_ms.toFixed(0)} ms`}
              </td>
              <td className="max-w-[260px] truncate px-4 py-3 text-xs text-destructive" title={row.error ?? ''}>
                {row.error ?? '-'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function MetricLineChart({
  data,
  metric,
  label,
  unit,
  color,
  dash,
  formatTime,
}: {
  data: MetricsPoint[];
  metric: 'calls' | 'errors' | 'latency_p95';
  label: string;
  unit: string;
  color: string;
  dash?: string;
  formatTime: (value?: string | null) => string;
}) {
  const values = data.map((row) => metric === 'latency_p95' ? row.latency_p95 ?? 0 : row[metric]);
  const max = Math.max(1, ...values);
  const points = values.map((value, index) => ({
    value,
    x: values.length <= 1 ? 0 : (index / (values.length - 1)) * 100,
    y: 44 - (value / max) * 40,
    ts: data[index]!.ts,
  }));
  const path = points.map((point, index) => `${index === 0 ? 'M' : 'L'} ${point.x.toFixed(2)} ${point.y.toFixed(2)}`).join(' ');
  return (
    <figure className="min-w-0 border-t border-edge-subtle pt-3 first:border-t-0 first:pt-0 md:border-l md:border-t-0 md:pl-4 md:pt-0 md:first:border-l-0 md:first:pl-0">
      <figcaption className="mb-2 flex items-baseline justify-between gap-3">
        <span className="text-sm font-medium">{label}</span>
        <span className="text-xs tabular-nums text-muted-foreground">{formatNumber(max)} {unit}</span>
      </figcaption>
      <svg viewBox="0 0 100 48" className="h-28 w-full overflow-visible" role="img" aria-label={`${label}; maximum ${max} ${unit}`}>
        <path d="M 0 4 H 100 M 0 24 H 100 M 0 44 H 100" fill="none" stroke="currentColor" strokeWidth="0.6" className="text-edge-subtle" vectorEffect="non-scaling-stroke" />
        <path d={path} fill="none" stroke="currentColor" strokeWidth="1.8" strokeDasharray={dash} className={color} vectorEffect="non-scaling-stroke" />
        {points.map((point) => (
          <circle
            key={point.ts}
            cx={point.x}
            cy={point.y}
            r="1.5"
            tabIndex={0}
            className={color}
            fill="currentColor"
            aria-label={`${formatTime(point.ts)}: ${point.value} ${unit}`}
          >
            <title>{formatTime(point.ts)}: {point.value} {unit}</title>
          </circle>
        ))}
      </svg>
    </figure>
  );
}

function MonitoringTab({ depId, active }: { depId: string; active: boolean }) {
  const { t } = useTranslation();
  const formatTime = useFormatDateTime();
  const query = useQuery({
    queryKey: ['deployment-metrics', depId, 'last-24-hours'],
    queryFn: () => getMetrics(depId, { ...last24HoursRange(), bucket: 'hour' }),
    enabled: active,
    refetchOnWindowFocus: false,
    refetchInterval: active ? 10_000 : false,
  });
  const series = query.data?.series ?? [];

  if (!active) return null;
  if (query.isLoading) return <div className="empty-state">{t('tasks.loading', 'Loading...')}</div>;
  if (query.isError) {
    return (
      <div className="rounded-md border border-destructive/40 bg-destructive/10 p-4 text-sm text-destructive">
        {t('deployments.detail.metricsError', 'Failed to load metrics.')}
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <section className="border-y border-edge-subtle py-4">
        <div className="flex items-center justify-between gap-3">
          <div>
            <h2 className="text-sm font-semibold">{t('deployments.detail.metricsChart', 'Requests, errors, and latency')}</h2>
            <p className="mt-1 text-xs text-muted-foreground">
              {t('deployments.detail.metricsWindow', 'Last 24 hours, hourly buckets')}
            </p>
          </div>
        </div>
        {series.length === 0 ? (
          <div className="empty-state">
            <div className="empty-state-title">{t('deployments.detail.noMetrics', 'No metrics in this window.')}</div>
          </div>
        ) : (
          <div className="mt-4 grid gap-4 md:grid-cols-3">
            <MetricLineChart data={series} metric="calls" label={t('deployments.detail.requests', 'Requests')} unit={t('deployments.detail.callsUnit', 'calls')} color="text-state-info" formatTime={formatTime} />
            <MetricLineChart data={series} metric="errors" label={t('deployments.detail.errors', 'Errors')} unit={t('deployments.detail.errorsUnit', 'errors')} color="text-state-danger" dash="4 2" formatTime={formatTime} />
            <MetricLineChart data={series} metric="latency_p95" label={t('deployments.detail.p95Latency', 'P95 latency')} unit="ms" color="text-state-warning" dash="1.5 1.5" formatTime={formatTime} />
          </div>
        )}
      </section>
      {series.length > 0 && (
        <div className="overflow-x-auto border-y border-edge-subtle">
          <table className="w-full text-sm">
            <thead className="border-b bg-surface-sunken text-left text-xs font-medium text-muted-foreground">
              <tr>
                <th className="px-4 py-3 font-medium">{t('deployments.detail.bucket', 'Bucket')}</th>
                <th className="px-4 py-3 text-right font-medium">{t('deployments.detail.calls', 'Calls')}</th>
                <th className="px-4 py-3 text-right font-medium">{t('deployments.detail.errors', 'Errors')}</th>
                <th className="px-4 py-3 text-right font-medium">P50</th>
                <th className="px-4 py-3 text-right font-medium">P95</th>
              </tr>
            </thead>
            <tbody>
              {series.map((row) => (
                <tr key={row.ts} className="border-b last:border-b-0">
                  <td className="px-4 py-3 font-mono text-xs">{formatTime(row.ts)}</td>
                  <td className="px-4 py-3 text-right tabular-nums">{row.calls}</td>
                  <td className="px-4 py-3 text-right tabular-nums text-destructive">{row.errors}</td>
                  <td className="px-4 py-3 text-right tabular-nums">{row.latency_p50 == null ? '-' : row.latency_p50.toFixed(1)}</td>
                  <td className="px-4 py-3 text-right tabular-nums">{row.latency_p95 == null ? '-' : row.latency_p95.toFixed(1)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function TestTab({ depId }: { depId: string }) {
  const { t } = useTranslation();
  const [inputsText, setInputsText] = useState('{}');
  const [output, setOutput] = useState<string | null>(null);
  const [parseError, setParseError] = useState<string | null>(null);
  const mutation = useMutation({
    mutationFn: (inputs: unknown) => testInvoke(depId, inputs),
    onSuccess: (resp) => setOutput(JSON.stringify(resp, null, 2)),
    onError: (e) => {
      setOutput(null);
      toast.error(e instanceof Error ? e.message : String(e));
    },
  });
  const onRun = () => {
    try {
      const parsed = JSON.parse(inputsText);
      setParseError(null);
      mutation.mutate(parsed);
    } catch (error) {
      setParseError(
        `${t('deployments.detail.testInvalidJson', 'Inputs are not valid JSON.')} ${
          error instanceof Error ? error.message : String(error)
        }`,
      );
    }
  };
  return (
    <div className="space-y-4">
      <section className="border-y border-edge-subtle py-4">
        <Label htmlFor="dep-test-inputs">{t('deployments.detail.testInputs', 'Inputs (JSON)')}</Label>
        <Textarea
          id="dep-test-inputs"
          rows={8}
          value={inputsText}
          onChange={(event) => setInputsText(event.target.value)}
          className="mt-2 font-mono text-xs"
        />
        {parseError && <div className="mt-2 text-xs text-destructive">{parseError}</div>}
        <div className="mt-3">
          <Button onClick={onRun} disabled={mutation.isPending}>
            <Play className="mr-2 h-4 w-4" />
            {t('deployments.testInvoke.run', 'Run')}
          </Button>
        </div>
      </section>
      {output && (
        <section className="border-y border-edge-subtle py-4">
          <Label>{t('deployments.detail.testOutput', 'Output')}</Label>
          <pre className="mt-2 max-h-96 overflow-auto rounded-md border bg-muted/40 p-3 font-mono text-xs">
            {output}
          </pre>
        </section>
      )}
    </div>
  );
}

function SecurityTab({ dep }: { dep: Deployment }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [rotated, setRotated] = useState<string | null>(null);
  const rotateMutation = useMutation({
    mutationFn: () => rotateKey(dep.id),
    onSuccess: (resp) => setRotated(resp.api_key),
    onError: (e) => toast.error(e instanceof Error ? e.message : String(e)),
  });

  return (
    <div className="space-y-4">
      <section className="border-y border-edge-subtle py-4">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <div className="flex items-center gap-2">
              <ShieldCheck className="h-4 w-4 text-state-success" />
              <h2 className="text-sm font-semibold">{t('deployments.detail.securityModel', 'Access control')}</h2>
            </div>
            <p className="mt-2 text-sm text-muted-foreground">
              {dep.trigger_type === 'api'
                ? t('deployments.detail.apiKeyHint', 'This API deployment uses a bearer key. The key is only shown at creation or rotation.')
                : t('deployments.detail.webhookHint', 'This webhook deployment uses an HMAC signing secret shown only at creation.')}
            </p>
          </div>
          {dep.trigger_type === 'api' && (
            <Button variant="outline" onClick={() => rotateMutation.mutate()} disabled={rotateMutation.isPending}>
              <KeyRound className="mr-2 h-4 w-4" />
              {t('deployments.detail.rotateKey', 'Rotate API key')}
            </Button>
          )}
        </div>
        <div className="mt-4 max-w-xl">
          <Label>{dep.trigger_type === 'api'
            ? t('deployments.create.apiKey', 'API Key')
            : t('deployments.create.hmacSecret', 'HMAC Secret')}</Label>
          <Input
            type="password"
            value="credential-is-not-retrievable"
            readOnly
            disabled
            className="mt-2 font-mono"
            aria-label={t('deployments.secret.stored', 'Stored credential')}
          />
          <p className="mt-2 text-xs text-muted-foreground">
            {t(
              'deployments.secret.notRetrievable',
              'The existing credential cannot be retrieved. Rotate it to receive a new one-time value.',
            )}
          </p>
        </div>
      </section>

      <section className="border-y border-edge-subtle py-4">
        <h2 className="text-sm font-semibold">{t('deployments.detail.rateLimitPolicy', 'Rate limit policy')}</h2>
        <p className="mt-2 text-sm text-muted-foreground">
          {t('deployments.detail.rateLimitHint', 'Requests over the configured QPS limit should be rejected with HTTP 429. Retry-After support belongs to the gateway/runtime implementation.')}
        </p>
      </section>

      <Dialog
        open={!!rotated}
        onOpenChange={(open) => {
          if (!open) {
            setRotated(null);
            void qc.invalidateQueries({ queryKey: ['deployment', dep.id] });
          }
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t('deployments.detail.rotateTitle', 'New API key')}</DialogTitle>
            <DialogDescription>
              {t(
                'deployments.create.oneTimeSecret',
                'This secret is shown only once. Copy it now - you cannot retrieve it later.',
              )}
            </DialogDescription>
          </DialogHeader>
          {rotated ? (
            <OneTimeSecretField
              value={rotated}
              label={t('deployments.create.apiKey', 'API Key')}
              testId="rotated-key"
            />
          ) : null}
          <DialogFooter>
            <Button onClick={() => setRotated(null)}>{t('deployments.create.close', 'Close')}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

export function DeploymentDetailPage() {
  const { t } = useTranslation();
  const formatTime = useFormatDateTime();
  const { depId } = useParams<{ depId: string }>();
  const [searchParams, setSearchParams] = useSearchParams();
  const [shareOpen, setShareOpen] = useState(false);
  const requestedTab = searchParams.get('tab');
  const tab: TabKey = ['overview', 'config', 'code', 'runs', 'monitoring', 'test', 'security'].includes(requestedTab ?? '')
    ? requestedTab as TabKey
    : 'overview';
  const setTab = (nextTab: TabKey) => {
    const next = new URLSearchParams(searchParams);
    next.set('tab', nextTab);
    setSearchParams(next, { replace: true });
  };
  const query = useQuery({
    queryKey: ['deployment', depId],
    queryFn: () => getDeployment(depId!),
    enabled: !!depId,
    refetchOnWindowFocus: false,
  });
  const metricsQuery = useQuery({
    queryKey: ['deployment-metrics', depId, 'last-24-hours', 'summary'],
    queryFn: () => getMetrics(depId!, { ...last24HoursRange(), bucket: 'hour' }),
    enabled: !!depId && !!query.data?.access?.capabilities.includes('inspect_runs'),
    refetchOnWindowFocus: false,
    refetchInterval: 15_000,
  });

  if (!depId) {
    return <div className="flex-1 p-6 text-sm text-muted-foreground">{t('deployments.detail.missingId', 'Missing deployment id in URL')}</div>;
  }
  if (query.isLoading) {
    return <div className="flex-1 p-6 text-sm text-muted-foreground">{t('tasks.loading', 'Loading...')}</div>;
  }
  if (query.isError || !query.data) {
    return (
      <div className="flex-1 p-6">
        <ActionableError
          title={t('deployments.detail.loadError', 'Failed to load this deployment.')}
          description={t('deployments.detail.loadErrorHint', 'Return to the deployment list or try loading this deployment again.')}
          actionLabel={t('retry', 'Retry')}
          onAction={() => void query.refetch()}
          technicalDetails={query.error instanceof Error ? query.error.message : String(query.error ?? '')}
          technicalDetailsLabel={t('common.technicalDetails', 'Technical details')}
        />
        <Link to="/deployments" className="mt-4 inline-flex text-sm text-primary underline-offset-4 hover:underline">
          {t('deployments.detail.backToList', 'Back to deployments')}
        </Link>
      </div>
    );
  }

  const dep = query.data;
  const capabilities = new Set(dep.access?.capabilities ?? []);
  const canUpdate = capabilities.has('update');
  const canInspectRuns = capabilities.has('inspect_runs');
  const canExecute = capabilities.has('execute');
  const canManageSecret = capabilities.has('manage_secret');
  const allowedTab = tab === 'config'
    ? canUpdate
    : tab === 'runs' || tab === 'monitoring'
      ? canInspectRuns
      : tab === 'test'
        ? canExecute
        : tab === 'security'
          ? canManageSecret
          : true;
  const activeTab: TabKey = allowedTab ? tab : 'overview';
  const endpoint = endpointFor(dep);
  const latestMetric = metricsQuery.data?.series.at(-1) ?? null;
  const versionLabel = dep.version_pin === 'head'
    ? t('deployments.detail.latestVersion', 'Latest version')
    : `v${dep.pinned_major ?? 0}.sv${dep.pinned_sub ?? 0}`;
  const healthStatus = latestMetric && latestMetric.calls > 0 && latestMetric.errors > 0
    ? 'warning'
    : dep.enabled
      ? 'success'
      : 'neutral';

  return (
    <EntityDetailShell
      resourceKind="deployment"
      backTo="/deployments"
      backLabel={t('deployments.detail.backToList', 'Back to deployments')}
      title={dep.name}
      icon={Rocket}
      status={<div className="flex flex-wrap items-center gap-2">
        <StatusBadge status={dep.enabled ? 'success' : 'neutral'}>
          {dep.enabled ? t('deployments.status.active', 'Active') : t('deployments.status.disabled', 'Disabled')}
        </StatusBadge>
        <StatusBadge status={healthStatus}>
          {latestMetric && latestMetric.errors > 0
            ? t('deployments.detail.healthAttention', 'Health needs attention')
            : dep.enabled
              ? t('deployments.detail.healthy', 'Healthy')
              : t('deployments.detail.inactive', 'Inactive')}
        </StatusBadge>
      </div>}
      metadata={<>
        <span>{triggerLabel(dep)}</span>
        <span>{versionLabel}</span>
        <span>{t('deployments.detail.updated', 'Updated')}: {formatTime(dep.updated_at ?? dep.created_at)}</span>
        <code className="max-w-[440px] truncate font-mono">{endpoint}</code>
        <CopyButton value={endpoint} label={t('deployments.actions.copyEndpoint', 'Copy endpoint')} />
      </>}
      actions={<>
            <Button
              variant="outline"
              size="sm"
              onClick={() => void Promise.all([
                query.refetch(),
                metricsQuery.refetch(),
              ])}
            >
              <RefreshCw className="mr-2 h-4 w-4" />
              {t('refresh', 'Refresh')}
            </Button>
            {canExecute ? (
              <Button size="sm" onClick={() => setTab('test')}>
                <Play className="mr-2 h-4 w-4" />
                {t('deployments.detail.tabs.test', 'Test')}
              </Button>
            ) : null}
            {capabilities.has('manage_access') ? (
              <Button variant="outline" size="sm" onClick={() => setShareOpen(true)}>
                <Share2 className="mr-2 h-4 w-4" />
                {t('deployments.action.share', 'Share deployment')}
              </Button>
            ) : null}
          </>}
    >

        <Tabs value={activeTab} onValueChange={(value) => setTab(value as TabKey)} className="space-y-4">
          <TabsList variant="underline" className="flex w-full flex-wrap justify-start">
            <TabsTrigger value="overview">{t('deployments.detail.tabs.overview', 'Overview')}</TabsTrigger>
            {canUpdate ? <TabsTrigger value="config">{t('deployments.detail.tabs.config', 'Config')}</TabsTrigger> : null}
            <TabsTrigger value="code">{t('deployments.detail.tabs.code', 'Code examples')}</TabsTrigger>
            {canInspectRuns ? <TabsTrigger value="runs">{t('deployments.detail.tabs.runs', 'Runs / Logs')}</TabsTrigger> : null}
            {canInspectRuns ? <TabsTrigger value="monitoring">{t('deployments.detail.tabs.monitoring', 'Monitoring')}</TabsTrigger> : null}
            {canExecute ? <TabsTrigger value="test">{t('deployments.detail.tabs.test', 'Test')}</TabsTrigger> : null}
            {canManageSecret ? <TabsTrigger value="security">{t('deployments.detail.tabs.security', 'Security')}</TabsTrigger> : null}
          </TabsList>
          <TabsContent value="overview">
            <OverviewTab
              dep={dep}
              latestMetric={latestMetric}
              onCopyEndpoint={() =>
                copy(
                  endpoint,
                  t('deployments.actions.copied', 'Copied'),
                  t('deployments.actions.copyFailed', 'Copy failed'),
                )
              }
              onTest={canExecute ? () => setTab('test') : undefined}
            />
          </TabsContent>
          <TabsContent value="config">
            <ConfigTab dep={dep} />
          </TabsContent>
          <TabsContent value="code">
            <CodeExamplesTab dep={dep} />
          </TabsContent>
          <TabsContent value="runs">
            <RunsTab depId={depId} active={activeTab === 'runs'} />
          </TabsContent>
          <TabsContent value="monitoring">
            <MonitoringTab depId={depId} active={activeTab === 'monitoring'} />
          </TabsContent>
          <TabsContent value="test">
            <TestTab depId={depId} />
          </TabsContent>
          <TabsContent value="security">
            <SecurityTab dep={dep} />
          </TabsContent>
        </Tabs>
        <ResourceShareDialog
          open={shareOpen}
          onOpenChange={setShareOpen}
          resourceKind="deployment"
          resourceId={dep.id}
          resourceName={dep.name}
          effectiveRole={dep.access?.effective_role}
          accessSource={dep.access?.source}
        />
    </EntityDetailShell>
  );
}
