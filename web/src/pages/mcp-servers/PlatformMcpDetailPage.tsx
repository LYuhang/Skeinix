import { useTranslation } from 'react-i18next';
import { useParams, useSearchParams } from 'react-router';
import {
  ArrowRight,
  Braces,
  PlugZap,
  ShieldCheck,
  Zap,
} from 'lucide-react';

import { EntityDetailShell } from '@/components/layout/entity-detail-shell';
import { Button } from '@/components/ui/button';
import { StatusBadge } from '@/components/ui/status';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { usePlatformMcpServices } from '@/lib/api/queries/mcp-servers';
import { McpToolDirectory } from './McpToolDirectory';
import { ActionableError } from '@/components/presentation/ActionableError';

export function PlatformMcpDetailPage() {
  const { t } = useTranslation();
  const { platformId } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const query = usePlatformMcpServices();
  const service = query.data?.find((item) => item.id === platformId);

  if (query.isLoading) {
    return (
      <div className="page-shell page-shell-contained">
        <div className="page-content mx-auto w-full max-w-6xl">
          <div className="rounded-lg border border-edge-subtle bg-surface-work p-10 text-center text-sm text-muted-foreground">
            {t('mcp.platform.loading', 'Loading platform capabilities…')}
          </div>
        </div>
      </div>
    );
  }

  if (query.isError || !service) {
    return (
      <EntityDetailShell
        resourceKind="mcp"
        backTo="/mcp-servers"
        backLabel={t('mcp.back', 'Back to MCP servers')}
        title={t('mcp.platform.not_found', 'Platform MCP not found')}
        icon={PlugZap}
      >
        <ActionableError
          title={query.isError
            ? t('mcp.platform.load_error', 'Could not load platform MCP capabilities.')
            : t('mcp.platform.not_found_hint', 'This built-in capability is not registered by the current platform version.')}
          description={query.isError ? t('mcp.platform.load_error_hint', 'Check the platform service and try loading its tool directory again.') : undefined}
          actionLabel={query.isError ? t('retry', 'Retry') : undefined}
          onAction={query.isError ? () => void query.refetch() : undefined}
          technicalDetails={query.error instanceof Error ? query.error.message : undefined}
          technicalDetailsLabel={t('common.technicalDetails', 'Technical details')}
        />
      </EntityDetailShell>
    );
  }

  const runtimeNames = service.runtime_types
    .map((runtime) => runtime === 'langchain' ? 'LangChain' : 'Codex')
    .join(' · ');
  const localizedName = t(`mcp.platform.${service.id}.name`, service.name);
  const localizedDescription = t(`mcp.platform.${service.id}.description`, service.description);
  const activationLabel = service.activation_mode === 'base'
    ? t('mcp.platform.always_available', 'Always available')
    : service.activation;
  const requestedTab = searchParams.get('tab');
  const activeTab = requestedTab === 'tools' ? 'tools' : 'overview';
  const setActiveTab = (tab: string) => {
    const next = new URLSearchParams(searchParams);
    next.set('tab', tab);
    setSearchParams(next, { replace: true });
  };

  return (
    <EntityDetailShell
      resourceKind="mcp"
      backTo="/mcp-servers"
      backLabel={t('mcp.back', 'Back to MCP servers')}
      title={localizedName}
      description={localizedDescription}
      icon={PlugZap}
      status={<StatusBadge status="success">{t('mcp.status.available', 'Available')}</StatusBadge>}
      metadata={(
        <>
          <span>{t('mcp.platform.builtin', 'Built-in Platform MCP')}</span>
          <span>{runtimeNames}</span>
          <span>{t('mcp.tools_count', { count: service.tools.length, defaultValue: '{{count}} Tools' })}</span>
        </>
      )}
      className="flex h-full min-h-0 flex-col"
    >
      <Tabs
        value={activeTab}
        onValueChange={setActiveTab}
        className="flex min-h-0 flex-1 flex-col overflow-hidden border-y border-edge-subtle bg-surface-work"
      >
        <TabsList variant="underline" className="h-auto w-full shrink-0 justify-start px-5">
          <TabsTrigger value="overview" className="py-3">
            {t('mcp.platform.detail.tab.overview', 'Overview')}
          </TabsTrigger>
          <TabsTrigger value="tools" className="py-3" data-testid="platform-mcp-tools-tab">
            {t('mcp.detail.tab.tools', 'Tools')} {service.tools.length}
          </TabsTrigger>
        </TabsList>

        <TabsContent value="overview" className="page-scroll-region m-0 min-h-0 flex-1 p-5 data-[state=inactive]:hidden">
          <div className="grid gap-4 md:grid-cols-3">
            <section className="rounded-xl border border-edge-subtle bg-surface-raised p-4">
              <div className="flex items-center gap-2 text-sm font-medium"><Zap className="h-4 w-4 text-primary" />{t('mcp.platform.detail.activation', 'Activation')}</div>
              <div className="mt-3 font-mono text-sm text-content-primary">{activationLabel}</div>
              <p className="mt-1 text-xs leading-5 text-muted-foreground">
                {service.activation_mode === 'base'
                  ? t('mcp.platform.detail.base_hint', 'The Runtime receives this capability on every eligible Turn.')
                  : t('mcp.platform.detail.command_hint', 'The Runtime receives this capability after the command is activated for the Chat.')}
              </p>
            </section>
            <section className="rounded-xl border border-edge-subtle bg-surface-raised p-4">
              <div className="flex items-center gap-2 text-sm font-medium"><Braces className="h-4 w-4 text-primary" />{t('mcp.platform.detail.runtimes', 'Runtimes')}</div>
              <div className="mt-3 text-sm text-content-primary">{runtimeNames}</div>
              <p className="mt-1 text-xs leading-5 text-muted-foreground">{t('mcp.platform.detail.runtime_hint', 'The same MCP protocol and tool schemas are used across the listed Runtimes.')}</p>
            </section>
            <section className="rounded-xl border border-edge-subtle bg-surface-raised p-4">
              <div className="flex items-center gap-2 text-sm font-medium"><ShieldCheck className="h-4 w-4 text-primary" />{t('mcp.platform.detail.security', 'Security boundary')}</div>
              <p className="mt-3 text-xs leading-5 text-muted-foreground">{t('mcp.platform.detail.security_hint', 'Calls use short-lived, Turn-bound capabilities and are re-authorized against the active user and Chat. Internal endpoints and tokens are never shown here.')}</p>
            </section>
          </div>

          <section className="mt-5 flex flex-col gap-4 rounded-xl border border-edge-subtle bg-surface-raised p-5 sm:flex-row sm:items-center sm:justify-between">
            <div className="max-w-2xl">
              <h2 className="font-semibold text-content-primary">{t('mcp.platform.detail.tools_intro_title', 'Understand the tools exposed to agents')}</h2>
              <p className="mt-1 text-sm leading-6 text-muted-foreground">{t('mcp.platform.detail.tools_intro', 'The Tools page documents the backend registry used by every supported Runtime, including parameters and read, write, or external-access hints.')}</p>
            </div>
            <Button variant="outline" className="shrink-0" onClick={() => setActiveTab('tools')}>
              {t('mcp.platform.detail.browse_tools', 'Browse tools')}
              <ArrowRight />
            </Button>
          </section>
        </TabsContent>

        <TabsContent value="tools" className="m-0 flex min-h-0 flex-1 flex-col overflow-hidden p-0 data-[state=inactive]:hidden">
          <div className="shrink-0 border-b border-edge-subtle bg-surface-raised/70 px-5 py-4">
            <div className="max-w-2xl">
              <h2 className="font-semibold text-content-primary">{t('mcp.platform.detail.tools', 'Registered tools')}</h2>
              <p className="mt-1 text-xs leading-5 text-muted-foreground">{t('mcp.platform.detail.tools_hint', 'Read-only documentation generated from the same backend schemas delivered to agents.')}</p>
            </div>
          </div>
          <McpToolDirectory tools={service.tools} />
        </TabsContent>
      </Tabs>
    </EntityDetailShell>
  );
}
