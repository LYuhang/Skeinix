/**
 * Settings panel for workspace LLM credentials.
 *
 * Lists the tenant's saved LLM API credentials (name / description / provider
 * from the PUBLIC projection) with per-row Edit / Delete. Provider keys are
 * write-only: the browser can create or replace them but cannot read them back.
 *
 * Loading / error / empty states + table styling mirror `DeploymentsListPage`
 * and `McpServersTab` so the management pages feel like one product.
 */
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Plus } from 'lucide-react';
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
  useDeleteLlmCredential,
  useLlmCredentials,
} from '@/lib/api/queries/llm-credentials';
import type { CredentialPublic } from '@/lib/api/llm-credentials';
import { CredentialFormDialog } from '@/pages/credentials/CredentialFormDialog';
import { CredentialRow } from '@/pages/credentials/CredentialRow';
import { OpenRouterConnectionPanel } from '@/pages/credentials/OpenRouterConnectionPanel';
import { ActionableError } from '@/components/presentation/ActionableError';
import { CompactEmptyState } from '@/components/presentation/CompactEmptyState';
import { AsyncState } from '@/components/ui/async-state';

export function CredentialsSettingsPanel() {
  const { t } = useTranslation();
  const query = useLlmCredentials();
  const deleteMutation = useDeleteLlmCredential();

  const [formOpen, setFormOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<CredentialPublic | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<CredentialPublic | null>(
    null,
  );

  const openCreate = () => {
    setEditTarget(null);
    setFormOpen(true);
  };
  const openEdit = (cred: CredentialPublic) => {
    setEditTarget(cred);
    setFormOpen(true);
  };

  const handleDelete = async () => {
    if (!confirmDelete) return;
    try {
      await deleteMutation.mutateAsync(confirmDelete.id);
      toast.success(t('credentials.deleted', 'Credential deleted'));
      setConfirmDelete(null);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    }
  };

  const items = (query.data ?? []).filter(
    (credential) => credential.connection_kind !== 'openrouter_oauth',
  );

  return (
    <>
      <header
        className="flex min-w-0 flex-col gap-3 sm:flex-row sm:items-start sm:justify-between"
        data-testid="credentials-section-header"
      >
        <div className="min-w-0">
          <h2 className="text-base font-medium">
            {t('credentials.title', 'API credentials')}
          </h2>
          <p className="mt-0.5 max-w-[65ch] text-sm leading-6 text-muted-foreground">
            {t(
              'credentials.subtitle',
              'Store the LLM API keys this workspace uses. Keys are encrypted and cannot be read back after saving.',
            )}
          </p>
        </div>
        <Button
          className="shrink-0 self-start"
          onClick={openCreate}
          data-testid="cred-add-button"
        >
          <Plus className="size-4" aria-hidden="true" />
          {t('credentials.add', 'Add key')}
        </Button>
      </header>

      <OpenRouterConnectionPanel />

      {query.isLoading ? (
        <AsyncState
          kind="loading"
          title={t('credentials.loading', 'Loading…')}
        />
      ) : query.isError ? (
        <ActionableError
          title={t('credentials.load_error', 'Failed to load credentials.')}
          description={t(
            'credentials.load_error_hint',
            'Check the connection and load the key list again.',
          )}
          actionLabel={t('retry', 'Retry')}
          onAction={() => void query.refetch()}
          technicalDetails={
            query.error instanceof Error
              ? query.error.message
              : String(query.error)
          }
          technicalDetailsLabel={t(
            'common.technicalDetails',
            'Technical details',
          )}
        />
      ) : items.length === 0 ? (
        <div data-testid="cred-empty-state">
          <CompactEmptyState
            title={t('credentials.empty', 'No API keys yet')}
            description={t(
              'credentials.emptyHint',
              'Saved models become available to Chat and workflow nodes.',
            )}
            actionLabel={t('credentials.add', 'Add key')}
            onAction={openCreate}
          />
        </div>
      ) : (
        <div className="table-panel">
          <div
            className="overflow-x-auto"
            data-testid="credentials-table-scroll"
          >
            <table className="w-full min-w-[760px] text-sm">
            <thead className="border-b bg-surface-sunken/60 text-left text-xs text-content-secondary">
              <tr>
                <th className="px-3 py-2 font-medium">
                  {t('credentials.col.name', 'Name')}
                </th>
                <th className="px-3 py-2 font-medium">
                  {t('credentials.col.provider', 'Provider')}
                </th>
                <th className="px-3 py-2 font-medium">
                  {t('credentials.col.key', 'API key')}
                </th>
                <th className="px-3 py-2 font-medium">
                  {t('credentials.col.context', 'Context')}
                </th>
                <th className="px-3 py-2 font-medium">
                  {t('credentials.col.updated', 'Updated')}
                </th>
                <th className="px-3 py-2 font-medium">
                  {t('credentials.col.actions', 'Actions')}
                </th>
              </tr>
            </thead>
            <tbody>
              {items.map((cred) => (
                <CredentialRow
                  key={cred.id}
                  cred={cred}
                  onEdit={openEdit}
                  onDelete={setConfirmDelete}
                />
              ))}
            </tbody>
            </table>
          </div>
        </div>
      )}

      <CredentialFormDialog
        open={formOpen}
        onOpenChange={setFormOpen}
        target={editTarget}
      />

      <Dialog
        open={!!confirmDelete}
        onOpenChange={(o) => !o && setConfirmDelete(null)}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {t('credentials.delete_title', 'Delete this credential?')}
            </DialogTitle>
            <DialogDescription>
              {t(
                'credentials.delete_confirm',
                'This removes the stored API key. Workflows using it will stop working. This cannot be undone.',
              )}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setConfirmDelete(null)}
              disabled={deleteMutation.isPending}
            >
              {t('credentials.cancel', 'Cancel')}
            </Button>
            <Button
              variant="destructive"
              onClick={() => void handleDelete()}
              disabled={deleteMutation.isPending}
            >
              {t('credentials.delete', 'Delete')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
