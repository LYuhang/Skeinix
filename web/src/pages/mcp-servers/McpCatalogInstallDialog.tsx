import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Check, PlugZap, ShieldCheck } from 'lucide-react';
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
import {
  useCreateMcpServer,
  useInstallMcpCatalogItem,
  useTestMcpServer,
} from '@/lib/api/queries/mcp-servers';
import type { McpCatalogItem, McpServerInput } from '@/lib/api/mcp-servers';

export function McpCatalogInstallDialog({
  open,
  onOpenChange,
  candidate,
  input,
  onInstalled,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  candidate: McpCatalogItem | null;
  input: McpServerInput | null;
  onInstalled: (serverId: string) => void;
}) {
  const { t } = useTranslation();
  const createMutation = useCreateMcpServer();
  const testMutation = useTestMcpServer();
  const catalogInstallMutation = useInstallMcpCatalogItem();
  const [error, setError] = useState<string | null>(null);

  if (!candidate) return null;

  const handleInstall = async () => {
    setError(null);
    try {
      if (candidate.auth_mode === 'oauth') {
        const server = await catalogInstallMutation.mutateAsync({
          source: candidate.source,
          sourceId: candidate.source_id,
        });
        toast.success(
          t('mcp.catalog.installed_connection_required', {
            defaultValue: '{{name}} Installed. Connect An Account To Enable It.',
            name: candidate.name,
          }),
        );
        onOpenChange(false);
        onInstalled(server.id);
        return;
      }
      if (!input) return;
      const probe = await testMutation.mutateAsync(input);
      if (probe.status !== 'ok') {
        setError(
          t('mcp.catalog.install_probe_failed', {
            defaultValue: 'Connection Failed: {{status}}',
            status: probe.status,
          }),
        );
        return;
      }
      await createMutation.mutateAsync(input);
      toast.success(
        t('mcp.catalog.installed', {
          defaultValue: '{{name}} Installed',
          name: candidate.name,
        }),
      );
      onOpenChange(false);
    } catch (installError) {
      setError(installError instanceof Error ? installError.message : String(installError));
    }
  };

  const pending = testMutation.isPending || createMutation.isPending || catalogInstallMutation.isPending;
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>
            {t('mcp.catalog.install_title', {
              defaultValue: 'Install {{name}}',
              name: candidate.name,
            })}
          </DialogTitle>
          <DialogDescription>
            {t(
              'mcp.catalog.confirm_desc',
              'Confirm that this MCP server can make its tools available to your agents.',
            )}
          </DialogDescription>
        </DialogHeader>

        <div className="flex items-center gap-3 border-y py-4">
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-primary/10 text-primary">
            <PlugZap className="h-5 w-5" />
          </div>
          <div className="min-w-0">
            <div className="truncate font-medium">{candidate.name}</div>
            <div className="mt-0.5 text-xs text-muted-foreground">
              {candidate.source === 'official'
                ? t('mcp.source.official.name', 'Official MCP Registry')
                : t('mcp.source.smithery.name', 'Smithery')}
            </div>
          </div>
        </div>

        <div className="space-y-2 text-sm text-muted-foreground">
          <div className="flex items-start gap-2">
            <Check className="mt-0.5 h-4 w-4 shrink-0 text-state-success" />
            {candidate.auth_mode === 'oauth'
              ? t('mcp.catalog.confirm_oauth_install', 'Installs the server definition first. No external account is connected yet.')
              : t('mcp.catalog.confirm_config', 'Uses the connection and credentials reviewed on the Setup tab.')}
          </div>
          <div className="flex items-start gap-2">
            <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-state-success" />
            {candidate.auth_mode === 'oauth'
              ? t('mcp.catalog.confirm_oauth_connect_later', 'Connect your account from the Connection tab after installation.')
              : t('mcp.catalog.confirm_probe', 'Tests the connection before anything is installed.')}
          </div>
        </div>

        {error ? (
          <p className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
            {error}
          </p>
        ) : null}

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={pending}>
            {t('mcp.cancel', 'Cancel')}
          </Button>
          <Button onClick={() => void handleInstall()} disabled={(!input && candidate.auth_mode !== 'oauth') || pending}>
            {pending ? t('mcp.catalog.installing', 'Installing…') : t('mcp.catalog.install', 'Install')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
