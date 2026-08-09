/**
 * Typed-name destructive-delete confirmation.
 *
 * Mirrors GitHub's repo-delete pattern: the user must type the workflow name
 * exactly before the "Delete" button enables. This guards against
 * fat-finger deletes of an unrelated workflow when the kebab menu was opened
 * on the wrong card.
 *
 * The mutation is `useDeleteWorkflow()`, which already invalidates the
 * workspace list + toasts on success/error. After a successful delete we
 * close the dialog; the card unmounts on the next list refetch.
 */
import { useState, type MouseEvent } from 'react';
import { useTranslation } from 'react-i18next';
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
import { Button } from '@/components/ui/button';
import { useDeleteWorkflow } from '@/lib/api/mutations/workflows';
import type { components } from '@/lib/api/schema';

type WorkflowMetaOut = components['schemas']['WorkflowMetaOut'];

export interface DeleteWorkflowDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  wf: WorkflowMetaOut | null;
}

/**
 * Inner body — re-mounts via `key={wf.wf_id}` whenever the target workflow
 * changes (and unmounts entirely on close). That natural remount is what
 * resets the typed-name input, so the typed-state lives here as plain
 * `useState` with no resetting effects required.
 */
interface DeleteWorkflowDialogBodyProps {
  wf: WorkflowMetaOut;
  onOpenChange: (open: boolean) => void;
}

function selectWorkflowName(event: MouseEvent<HTMLSpanElement>) {
  const selection = window.getSelection();
  if (!selection) return;
  const range = document.createRange();
  range.selectNodeContents(event.currentTarget);
  selection.removeAllRanges();
  selection.addRange(range);
}

function DeleteWorkflowDialogBody({
  wf,
  onOpenChange,
}: DeleteWorkflowDialogBodyProps) {
  const { t } = useTranslation();
  const [typed, setTyped] = useState('');
  const deleteWorkflow = useDeleteWorkflow();
  const targetName = wf.workflow_name || wf.wf_id;
  const canConfirm = typed === targetName && !deleteWorkflow.isPending;

  const handleConfirm = async () => {
    await deleteWorkflow.mutateAsync(wf.wf_id);
    onOpenChange(false);
  };

  return (
    <DialogContent className="min-w-0 overflow-x-hidden">
      <DialogHeader className="min-w-0">
        <DialogTitle className="break-words">
          {t('workspace.delete.titlePrefix', 'Delete')} "{targetName}"?
        </DialogTitle>
        <DialogDescription>
          {t(
            'workspace.delete.descPrefix',
            'This will permanently delete the workflow',
          )}{' '}
          <span className="break-all font-medium">{targetName}</span>{' '}
          {t(
            'workspace.delete.descSuffix',
            'and every version in its history, including its workflow storage files. This action cannot be undone.',
          )}
        </DialogDescription>
      </DialogHeader>
      <div className="flex flex-col gap-2">
        <Label htmlFor="delete-workflow-confirm" className="min-w-0 leading-6">
          {t('workspace.delete.typePrefix', 'Type')}{' '}
          <span
            className="app-scrollbar inline-block max-w-full cursor-text select-text overflow-x-auto whitespace-nowrap rounded bg-muted/60 px-1 align-bottom font-mono font-medium"
            data-role="workflow-delete-copy-name"
            onDoubleClick={selectWorkflowName}
            title={targetName}
          >
            {targetName}
          </span>{' '}
          {t('workspace.delete.typeSuffix', 'to confirm')}
        </Label>
        <Input
          id="delete-workflow-confirm"
          autoFocus
          autoComplete="off"
          value={typed}
          onChange={(e) => setTyped(e.target.value)}
        />
      </div>
      <DialogFooter>
        <Button
          type="button"
          variant="outline"
          onClick={() => onOpenChange(false)}
        >
          {t('cancel', 'Cancel')}
        </Button>
        <Button
          type="button"
          variant="destructive"
          disabled={!canConfirm}
          onClick={handleConfirm}
        >
          {deleteWorkflow.isPending
            ? t('workspace.delete.deleting', 'Deleting…')
            : t('delete_wf', 'Delete')}
        </Button>
      </DialogFooter>
    </DialogContent>
  );
}

export function DeleteWorkflowDialog({
  open,
  onOpenChange,
  wf,
}: DeleteWorkflowDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      {wf ? (
        <DeleteWorkflowDialogBody
          key={wf.wf_id}
          wf={wf}
          onOpenChange={onOpenChange}
        />
      ) : null}
    </Dialog>
  );
}
