/**
 * "Duplicate workflow" modal.
 *
 * Replaces the old silent copy: clicking Duplicate on a row now opens this
 * dialog so the user can name + describe the copy before it is created.
 *
 * Defaults are seeded from the source row: name → `{orig} (copy)`,
 * description → the source's description (carried on the list row as
 * `wf.description`; defaults to empty when absent — we do NOT fetch extra
 * meta here since the list already provides it). On confirm we fire the
 * existing `useDuplicateWorkflow` (GET-snapshot → create → commit) with the
 * user-entered name + description instead of the hardcoded `(copy)` shape.
 *
 * Because the Radix-portalled `DialogContent` unmounts on close, the form is
 * re-seeded whenever the open dialog targets a different workflow so reopening
 * on a different row does not leak the previous values.
 */
import { useEffect } from 'react';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
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
import { Textarea } from '@/components/ui/textarea';
import { Label } from '@/components/ui/label';
import { Button } from '@/components/ui/button';
import { useDuplicateWorkflow } from '@/lib/api/mutations/workflows';
import type { components } from '@/lib/api/schema';

type WorkflowMetaOut = components['schemas']['WorkflowMetaOut'];

const schema = z.object({
  name: z.string().min(1, 'Name is required'),
  description: z.string().optional(),
});

type FormValues = z.infer<typeof schema>;

export interface DuplicateWorkflowDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  wf: WorkflowMetaOut | null;
}

export function DuplicateWorkflowDialog({
  open,
  onOpenChange,
  wf,
}: DuplicateWorkflowDialogProps) {
  const { t } = useTranslation();
  const duplicate = useDuplicateWorkflow();

  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: { name: '', description: '' },
  });

  // Re-seed defaults whenever the open dialog targets a different workflow.
  useEffect(() => {
    if (open && wf) {
      const base = wf.workflow_name || wf.wf_id;
      form.reset({
        name: `${base} (copy)`,
        description: wf.description ?? '',
      });
    }
  }, [open, wf, form]);

  if (!wf) return null;

  const onSubmit = form.handleSubmit(async (values) => {
    await duplicate.mutateAsync({
      wfId: wf.wf_id,
      name: values.name,
      description: values.description ?? '',
    });
    onOpenChange(false);
  });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent data-testid="duplicate-dialog">
        <DialogHeader>
          <DialogTitle>
            {t('duplicate_dialog_title', 'Duplicate workflow')}
          </DialogTitle>
          <DialogDescription>
            {t(
              'duplicate_dialog_desc',
              'Create a copy of this workflow. Adjust the name and description before duplicating.',
            )}
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={onSubmit} className="flex flex-col gap-4">
          <div className="flex flex-col gap-2">
            <Label htmlFor="duplicate-workflow-name">
              {t('col_name', 'Name')}
            </Label>
            <Input
              id="duplicate-workflow-name"
              autoFocus
              data-testid="duplicate-name"
              aria-invalid={form.formState.errors.name ? true : undefined}
              {...form.register('name')}
            />
            {form.formState.errors.name ? (
              <p className="text-xs text-destructive">
                {form.formState.errors.name.message}
              </p>
            ) : null}
          </div>
          <div className="flex flex-col gap-2">
            <Label htmlFor="duplicate-workflow-description">
              {t('col_description', 'Description')}
            </Label>
            <Textarea
              id="duplicate-workflow-description"
              data-testid="duplicate-desc"
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
            <Button
              type="submit"
              data-testid="duplicate-confirm"
              disabled={duplicate.isPending}
            >
              {duplicate.isPending
                ? t('duplicating_wf', 'Duplicating…')
                : t('duplicate_wf', 'Duplicate')}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
