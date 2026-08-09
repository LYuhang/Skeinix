/**
 * "Edit info" modal — rename / re-describe an existing workflow.
 *
 * Defaults are sourced from the current `WorkflowMetaOut`. Because the
 * Radix-portalled `DialogContent` unmounts on close, `form.reset(defaults)`
 * is re-run whenever the target workflow changes so reopening on a
 * different card does not leak the previous values.
 *
 * Submitting calls `useUpdateWorkflowMeta().mutateAsync({wfId, ...vals})`,
 * which invalidates both `['workflow', wfId]` and `['workspace']`.
 */
import { useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { Label } from '@/components/ui/label';
import { Button } from '@/components/ui/button';
import { useUpdateWorkflowMeta } from '@/lib/api/mutations/workflows';
import type { components } from '@/lib/api/schema';

type WorkflowMetaOut = components['schemas']['WorkflowMetaOut'];

const schema = z.object({
  name: z.string().min(1, 'Name is required'),
  description: z.string().optional(),
});

type FormValues = z.infer<typeof schema>;

export interface EditWorkflowInfoDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  wf: WorkflowMetaOut | null;
}

export function EditWorkflowInfoDialog({
  open,
  onOpenChange,
  wf,
}: EditWorkflowInfoDialogProps) {
  const { t } = useTranslation();
  const updateMeta = useUpdateWorkflowMeta();

  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: { name: '', description: '' },
  });

  // Re-seed the form whenever the open dialog targets a different workflow,
  // or when the same target reopens after close (defaults may have changed
  // server-side since last edit).
  useEffect(() => {
    if (open && wf) {
      form.reset({
        name: wf.workflow_name,
        description: wf.description ?? '',
      });
    }
  }, [open, wf, form]);

  if (!wf) return null;

  const onSubmit = form.handleSubmit(async (values) => {
    await updateMeta.mutateAsync({
      wfId: wf.wf_id,
      name: values.name,
      description: values.description ?? '',
    });
    onOpenChange(false);
  });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>
            {t('workspace.edit.title', 'Edit workflow info')}
          </DialogTitle>
          <DialogDescription>
            {t(
              'workspace.edit.desc',
              'Update the name and description for this workflow.',
            )}
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={onSubmit} className="flex flex-col gap-4">
          <div className="flex flex-col gap-2">
            <Label htmlFor="edit-workflow-name">
              {t('workspace.field.name', 'Name')}
            </Label>
            <Input
              id="edit-workflow-name"
              autoFocus
              aria-invalid={form.formState.errors.name ? true : undefined}
              {...form.register('name')}
            />
            {form.formState.errors.name ? (
              <p className="text-xs text-destructive">
                {t('workspace.field.nameRequired', 'Name is required')}
              </p>
            ) : null}
          </div>
          <div className="flex flex-col gap-2">
            <Label htmlFor="edit-workflow-description">
              {t('workspace.field.description', 'Description')}
            </Label>
            <Textarea
              id="edit-workflow-description"
              rows={3}
              {...form.register('description')}
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
            <Button type="submit" disabled={updateMeta.isPending}>
              {updateMeta.isPending
                ? t('workspace.edit.saving', 'Saving…')
                : t('save', 'Save')}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
