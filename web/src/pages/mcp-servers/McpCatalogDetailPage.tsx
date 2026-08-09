import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router';
import {
  ArrowLeft,
  ExternalLink,
  LockKeyhole,
  PlugZap,
  Settings2,
} from 'lucide-react';

import { EntityDetailShell } from '@/components/layout/entity-detail-shell';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Switch } from '@/components/ui/switch';
import { StatusBadge } from '@/components/ui/status';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import {
  useMcpCatalogItem,
  useMcpServers,
} from '@/lib/api/queries/mcp-servers';
import type { McpServerInput } from '@/lib/api/mcp-servers';
import { formatNumber } from '@/lib/format/number';
import { McpCatalogInstallDialog } from './McpCatalogInstallDialog';
import { buildCatalogInstallInput } from './mcp-catalog';
import { ActionableError } from '@/components/presentation/ActionableError';

export function McpCatalogDetailPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { source: sourceParam } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const source = sourceParam === 'smithery' ? 'smithery' : 'official';
  const catalogSourceName = source === 'official'
    ? t('mcp.source.official.name', 'Official MCP Registry')
    : t('mcp.source.smithery.name', 'Smithery');
  const sourceId = searchParams.get('id') ?? undefined;
  const query = useMcpCatalogItem(source, sourceId);
  const installedQuery = useMcpServers();
  const [values, setValues] = useState<Record<string, string>>({});
  const [confirmOpen, setConfirmOpen] = useState(false);

  const item = query.data;
  useEffect(() => {
    queueMicrotask(() => setValues(
      item
        ? Object.fromEntries(
            item.config_fields
              .filter((field) => field.default !== null)
              .map((field) => [field.key, String(field.default)]),
          )
        : {},
    ));
  }, [item]);

  const installed = useMemo(() => installedQuery.data ?? [], [installedQuery.data]);
  const existing = useMemo(
    () =>
      item
        ? installed.find(
            (server) =>
              (server.source === item.source && server.source_id === item.source_id) ||
              server.name === item.name ||
              (!!item.connection && server.endpoint === item.connection.endpoint),
          )
        : undefined,
    [installed, item],
  );
  const missingRequired =
    item?.config_fields.some(
      (field) => field.required && !values[field.key]?.trim(),
    ) ?? false;
  const requiresOAuth = item?.auth_mode === 'oauth';

  const installInput = useMemo<McpServerInput | null>(() => {
    if (!item || !item.connection || missingRequired || requiresOAuth) return null;
    try {
      return buildCatalogInstallInput(item, installed, values);
    } catch {
      return null;
    }
  }, [installed, item, missingRequired, requiresOAuth, values]);

  const back = (
    <Link
      to={`/mcp-servers?tab=discover&source=${source}`}
      className="inline-flex items-center gap-1 text-sm text-muted-foreground transition-colors hover:text-foreground"
    >
      <ArrowLeft className="h-4 w-4" />
      {t('mcp.catalog.back', 'Back To Discover')}
    </Link>
  );

  if (query.isLoading) {
    return (
      <div className="page-shell page-shell-contained">
        <div className="page-content max-w-5xl">
          {back}
          <div className="empty-state">{t('mcp.catalog.loading', 'Loading Server Details…')}</div>
        </div>
      </div>
    );
  }
  if (query.isError || !item) {
    return (
      <div className="page-shell page-shell-contained">
        <div className="page-content max-w-5xl">
          {back}
          <ActionableError
            className="mt-4"
            title={t('mcp.catalog.detail_error', 'Server details could not be loaded from this source.')}
            description={t('mcp.catalog.detail_error_hint', 'Check the selected registry and try loading this server again.')}
            actionLabel={t('retry', 'Retry')}
            onAction={() => void query.refetch()}
            technicalDetails={query.error instanceof Error ? query.error.message : undefined}
            technicalDetailsLabel={t('common.technicalDetails', 'Technical details')}
          />
        </div>
      </div>
    );
  }

  const availableTabs = [
    'overview',
    ...(item.config_fields.length > 0 ? ['setup'] : []),
    ...(requiresOAuth ? ['connection'] : []),
    'security',
  ];
  const requestedTab = searchParams.get('tab');
  const activeTab = requestedTab && availableTabs.includes(requestedTab)
    ? requestedTab
    : 'overview';

  return (
    <>
      <EntityDetailShell
        resourceKind="mcp"
        backTo={`/mcp-servers?tab=discover&source=${source}`}
        backLabel={t('mcp.catalog.back', 'Back To Discover')}
        title={item.name}
        description={catalogSourceName}
        icon={PlugZap}
        status={item.verified ? <StatusBadge status="success">{t('mcp.catalog.verified', 'Verified')}</StatusBadge> : undefined}
        metadata={
          <>
            <span className="font-mono">{item.source_id}</span>
            {item.version ? <span>{t('mcp.catalog.version', 'Version')} {item.version}</span> : null}
            {item.connection ? <span>{item.connection.transport.replace('_', ' ')}</span> : null}
          </>
        }
        actions={existing ? (
            <Button asChild variant="outline">
              <Link to={`/mcp-servers/${existing.id}`}>{t('mcp.catalog.installed_button', 'Installed')}</Link>
            </Button>
          ) : (
            <Button
              onClick={() => setConfirmOpen(true)}
              disabled={requiresOAuth ? !item.connection : !installInput}
            >
              {t('mcp.catalog.install', 'Install')}
            </Button>
          )}
      >

        <Tabs
          value={activeTab}
          onValueChange={(tab) => {
            const next = new URLSearchParams(searchParams);
            next.set('tab', tab);
            setSearchParams(next, { replace: true });
          }}
          className="flex min-h-0 flex-1 flex-col gap-4"
        >
          <TabsList variant="underline" className="h-auto w-full shrink-0 justify-start">
            <TabsTrigger value="overview">{t('mcp.catalog.tab.overview', 'Overview')}</TabsTrigger>
            {item.config_fields.length > 0 ? (
              <TabsTrigger value="setup">{t('mcp.catalog.tab.setup', 'Setup')}</TabsTrigger>
            ) : null}
            {requiresOAuth ? (
              <TabsTrigger value="connection">{t('mcp.catalog.tab.connection', 'Connection')}</TabsTrigger>
            ) : null}
            <TabsTrigger value="security">{t('mcp.catalog.tab.security', 'Security')}</TabsTrigger>
          </TabsList>

          <TabsContent value="overview" className="page-scroll-region mt-0 min-h-0 flex-1 space-y-5 pr-2">
            <section className="border-b pb-5">
              <h2 className="text-sm font-semibold">{t('mcp.catalog.about', 'About')}</h2>
              <p className="mt-2 max-w-3xl text-sm leading-6 text-muted-foreground">
                {item.description || t('mcp.no_description', 'No Brief Description Saved Yet.')}
              </p>
            </section>
            <dl className="grid gap-4 text-sm sm:grid-cols-2">
              <div>
                <dt className="text-muted-foreground">{t('mcp.catalog.source', 'Source')}</dt>
                <dd className="mt-1 font-medium">{catalogSourceName}</dd>
              </div>
              <div>
                <dt className="text-muted-foreground">{t('mcp.catalog.transport', 'Connection')}</dt>
                <dd className="mt-1 font-medium">
                  {item.connection?.transport.replace('_', ' ') ?? t('mcp.catalog.not_supported', 'Not Available')}
                </dd>
              </div>
              {item.version ? (
                <div>
                  <dt className="text-muted-foreground">{t('mcp.catalog.version', 'Version')}</dt>
                  <dd className="mt-1 font-medium">{item.version}</dd>
                </div>
              ) : null}
              {item.usage_count != null ? (
                <div>
                  <dt className="text-muted-foreground">{t('mcp.catalog.uses', 'Uses')}</dt>
                  <dd className="mt-1 font-medium">{formatNumber(item.usage_count)}</dd>
                </div>
              ) : null}
            </dl>
            {item.homepage ? (
              <Button asChild variant="outline" size="sm">
                <a href={item.homepage} target="_blank" rel="noreferrer">
                  {t('mcp.catalog.open_source', 'Open Source Page')}
                  <ExternalLink className="h-4 w-4" />
                </a>
              </Button>
            ) : null}
          </TabsContent>

          {item.config_fields.length > 0 ? <TabsContent value="setup" className="page-scroll-region mt-0 min-h-0 flex-1 pr-2">
            <section className="max-w-2xl space-y-5">
              <div className="flex items-start gap-3 border-b pb-4">
                <Settings2 className="mt-0.5 h-5 w-5 text-muted-foreground" />
                <div>
                  <h2 className="text-sm font-semibold">{t('mcp.catalog.setup_title', 'Required Configuration')}</h2>
                  <p className="mt-1 text-sm text-muted-foreground">
                    {t('mcp.catalog.setup_help', 'These fields are declared by this MCP server. Connection details and internal names are configured automatically.')}
                  </p>
                  <p className="mt-1 text-xs text-muted-foreground">
                    {item.configuration_source === 'official_registry'
                      ? t('mcp.catalog.setup_source_official', 'Configuration schema from the Official MCP Registry')
                      : t('mcp.catalog.setup_source_smithery', 'Configuration schema published through Smithery')}
                  </p>
                </div>
              </div>
              {item.config_fields.map((field) => (
                  <div key={field.key} className="flex flex-col gap-1.5">
                    <Label htmlFor={`mcp-config-${field.key}`}>
                      {field.label}{field.required ? ' *' : ''}
                    </Label>
                    {field.choices.length > 0 ? (
                      <Select
                        value={values[field.key] ?? ''}
                        disabled={!!existing}
                        onValueChange={(value) =>
                          setValues((current) => ({ ...current, [field.key]: value }))
                        }
                      >
                        <SelectTrigger id={`mcp-config-${field.key}`}>
                          <SelectValue placeholder={field.placeholder || t('mcp.catalog.select_value', 'Select a value')} />
                        </SelectTrigger>
                        <SelectContent>
                          {field.choices.map((choice) => (
                            <SelectItem key={choice} value={choice}>{choice}</SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    ) : field.input_type === 'boolean' ? (
                      <div className="flex h-10 items-center gap-3 rounded-md border px-3">
                        <Switch
                          id={`mcp-config-${field.key}`}
                          checked={(values[field.key] ?? 'false') === 'true'}
                          disabled={!!existing}
                          onCheckedChange={(checked) =>
                            setValues((current) => ({ ...current, [field.key]: String(checked) }))
                          }
                        />
                        <span className="text-sm text-muted-foreground">
                          {(values[field.key] ?? 'false') === 'true'
                            ? t('mcp.catalog.enabled_value', 'Enabled')
                            : t('mcp.catalog.disabled_value', 'Disabled')}
                        </span>
                      </div>
                    ) : (
                      <Input
                        id={`mcp-config-${field.key}`}
                        type={field.secret ? 'password' : field.input_type === 'number' ? 'number' : 'text'}
                        placeholder={field.placeholder}
                        value={values[field.key] ?? ''}
                        disabled={!!existing}
                        onChange={(event) =>
                          setValues((current) => ({ ...current, [field.key]: event.target.value }))
                        }
                      />
                    )}
                    {field.description ? <p className="text-xs text-muted-foreground">{field.description}</p> : null}
                  </div>
                ))}
            </section>
          </TabsContent> : null}

          {requiresOAuth ? (
            <TabsContent value="connection" className="page-scroll-region mt-0 min-h-0 flex-1 pr-2">
              <section className="flex max-w-2xl items-start gap-3">
                <LockKeyhole className="mt-0.5 h-5 w-5 text-muted-foreground" />
                <div>
                  <h2 className="text-sm font-semibold">
                    {t('mcp.catalog.oauth_title', 'Account Connection Required')}
                  </h2>
                  <p className="mt-1 text-sm leading-6 text-muted-foreground">
                    {t(
                      'mcp.catalog.oauth_help',
                      'Install the server first, then connect your account from its Connection tab. The server stays unavailable to agents until authorization succeeds.',
                    )}
                  </p>
                </div>
              </section>
            </TabsContent>
          ) : null}

          <TabsContent value="security" className="page-scroll-region mt-0 min-h-0 flex-1 pr-2">
            <section className="flex max-w-2xl items-start gap-3">
              <LockKeyhole className="mt-0.5 h-5 w-5 text-muted-foreground" />
              <div>
                <h2 className="text-sm font-semibold">{t('mcp.catalog.security_title', 'Review Access Before Installing')}</h2>
                <p className="mt-1 text-sm leading-6 text-muted-foreground">
                  {t('mcp.catalog.security_help', 'MCP servers may read data or perform actions in external services. Verify the publisher and requested credentials before installing. The connection is tested before the server is saved.')}
                </p>
              </div>
            </section>
          </TabsContent>
        </Tabs>
      </EntityDetailShell>

      <McpCatalogInstallDialog
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
        candidate={item}
        input={installInput}
        onInstalled={(serverId) => navigate(`/mcp-servers/${serverId}`)}
      />
    </>
  );
}
