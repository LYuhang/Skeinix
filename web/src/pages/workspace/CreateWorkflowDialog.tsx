/**
 * "New workflow" modal.
 *
 * react-hook-form + zod for the name/description fields. On submit:
 *   1. Fires `useCreateWorkflow().mutateAsync(...)` — the mutation hook
 *      invalidates `['workspace']` and toasts on success/error.
 *   2. Resets the form and closes the dialog.
 *   3. Navigates to `/workflow/<wf_id>` so the user lands on the new canvas
 *      (the route itself arrives in T6; until then react-router shows the
 *      default boundary, which is acceptable for T5 verification).
 *
 * The "Create" button is disabled while the mutation is pending so a slow
 * backend cannot generate duplicate creates from a double-click.
 */
import { useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import { useNavigate } from 'react-router';
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
import { useCreateWorkflow } from '@/lib/api/mutations/workflows';

const schema = z.object({
  name: z.string().min(1, 'Name is required'),
  description: z.string().optional(),
});

type FormValues = z.infer<typeof schema>;

export interface CreateWorkflowDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function CreateWorkflowDialog({
  open,
  onOpenChange,
}: CreateWorkflowDialogProps) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const createWorkflow = useCreateWorkflow();

  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: { name: '', description: '' },
  });

  // Reset form whenever the dialog flips closed so the next open starts fresh
  // — react-hook-form does not auto-reset across unmount/remount cycles when
  // the dialog content is portalled and unmounted by Radix.
  useEffect(() => {
    if (!open) form.reset({ name: '', description: '' });
  }, [open, form]);

  const onSubmit = form.handleSubmit(async (values) => {
    const created = await createWorkflow.mutateAsync({
      name: values.name,
      description: values.description ?? '',
    });
    onOpenChange(false);
    if (created) navigate(`/workflow/${created.wf_id}`);
  });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t('workspace.create.title', 'New workflow')}</DialogTitle>
          <DialogDescription>
            {t(
              'workspace.create.desc',
              'Create a new workflow. You can edit name and description later.',
            )}
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={onSubmit} className="flex flex-col gap-4">
          <div className="flex flex-col gap-2">
            <Label htmlFor="new-workflow-name">
              {t('workspace.field.name', 'Name')}
            </Label>
            <Input
              id="new-workflow-name"
              autoFocus
              data-testid="create-workflow-name"
              placeholder={t('workspace.create.namePh', 'My workflow')}
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
            <Label htmlFor="new-workflow-description">
              {t('workspace.field.description', 'Description')}
            </Label>
            <Textarea
              id="new-workflow-description"
              placeholder={t('workspace.create.descPh', 'Optional description')}
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
            <Button type="submit" disabled={createWorkflow.isPending}>
              {createWorkflow.isPending
                ? t('workspace.create.creating', 'Creating…')
                : t('workspace.create.create', 'Create')}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
