/**
 * "Workflow check" modal (T16).
 *
 * Replaces the previous inline-toast result of the `useCheckWorkflow`
 * mutation in `CanvasToolbar`: the toolbar now opens this dialog, which
 * lazily fires the mutation on first open and renders the result inline.
 *
 * Lifecycle
 * ---------
 * The plan calls for a *mutation* (not a query) — Check is a server-side
 * action with side-effect-free idempotency but no caching semantics. We
 * trigger it from a `useEffect` keyed on `open`: each open → one fresh
 * check; closing the dialog drops the in-flight result naturally with the
 * mutation hook's local state.
 *
 * UI states
 * ---------
 *   - Pending: skeleton placeholder.
 *   - Success (status === 'success'): green check + "Workflow valid".
 *   - Success (status !== 'success'): red X + `error_message` (falling
 *     back to a generic "Check failed").
 *   - Network / 4xx / 5xx: red X + `errorMessage(error)`. The mutation's
 *     onError toast still fires for parity with other failure paths.
 */
import { useEffect } from 'react';
import { CheckCircle2, Copy, XCircle } from 'lucide-react';
import { toast } from 'sonner';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { useCheckWorkflow } from '@/lib/api/mutations/workflow-ops';
import { useWorkflowEditStore } from '@/stores/workflow-edit';
import { errorMessage } from '@/lib/api/mutations/error-message';
import { humanizeNodeRefs } from '@/lib/workflow/node-label';
import { formatCheckError } from './formatCheckError';

export interface WorkflowCheckDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  wfId: string;
}

export function WorkflowCheckDialog({
  open,
  onOpenChange,
  wfId,
}: WorkflowCheckDialogProps) {
  const check = useCheckWorkflow(wfId);

  // Fire-on-open. Re-running on `wfId` change covers the (rare) case of
  // a parent route swap that keeps the dialog mounted.
  useEffect(() => {
    if (open) {
      check.reset();
      // Validate the CURRENT DRAFT (unsaved edits included), not the committed
      // version — so the user can Check without saving first.
      const draft = useWorkflowEditStore.getState().draft ?? undefined;
      void check.mutateAsync(draft).catch(() => {
        // onError toast already raised — local state lives in `check.error`.
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, wfId]);

  const valid = check.data?.status === 'success';

  // The full error text shown (if any) — exposed via a one-click Copy button
  // AND made selectable (`select-text` below) so Ctrl+C works inside the modal.
  const errorText = check.isError
    ? errorMessage(check.error)
    : check.data && !valid
      ? check.data.error_message ?? ''
      : '';

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="select-text">
        <DialogHeader>
          <div className="flex items-start justify-between gap-2 pr-6">
            <div className="space-y-1">
              <DialogTitle>Workflow check</DialogTitle>
              <DialogDescription>
                Server-side validation of the current draft.
              </DialogDescription>
            </div>
            {errorText && (
              <Button
                variant="ghost"
                size="icon"
                className="h-7 w-7 shrink-0"
                aria-label="Copy error"
                title="Copy error"
                onClick={() => {
                  void navigator.clipboard.writeText(errorText);
                  toast.success('Error copied');
                }}
              >
                <Copy className="h-4 w-4" />
              </Button>
            )}
          </div>
        </DialogHeader>
        {check.isPending ? (
          <div className="flex flex-col gap-2" data-role="check-loading">
            <Skeleton className="h-6 w-1/3" />
            <Skeleton className="h-4 w-2/3" />
          </div>
        ) : check.isError ? (
          <div
            className="flex items-start gap-2 text-destructive"
            data-role="check-error"
          >
            <XCircle className="mt-0.5 h-5 w-5 shrink-0" />
            <p className="text-sm">{errorMessage(check.error)}</p>
          </div>
        ) : check.data ? (
          valid ? (
            <div
              className="flex items-center gap-2 text-state-success"
              data-role="check-ok"
            >
              <CheckCircle2 className="h-5 w-5 shrink-0" />
              <p className="text-sm font-medium">Workflow valid</p>
            </div>
          ) : (
            <div
              className="flex items-start gap-2 text-destructive"
              data-role="check-fail"
            >
              <XCircle className="mt-0.5 h-5 w-5 shrink-0" />
              <CheckFailBody raw={check.data.error_message} />
            </div>
          )
        ) : null}
      </DialogContent>
    </Dialog>
  );
}

/**
 * Render a failed-Check `error_message` legibly. The engine surfaces terse
 * jsonschema/assertion strings (often prefixed like `[StartNode Check]: `
 * and naming the offending node); `formatCheckError` peels those into a
 * short headline + detail + optional "what to fix" hint. Not a parser — just
 * makes the existing message readable.
 */
function CheckFailBody({ raw }: { raw: string | null | undefined }) {
  const { headline, detail, hint } = formatCheckError(raw);
  // Rewrite bare `node_<n>` ids in the displayed strings to `node_name(node_id)`
  // so the user can locate the offending node. The draft is the id→name source.
  const draft = useWorkflowEditStore.getState().draft;
  return (
    <div className="min-w-0 space-y-1">
      <p className="text-sm font-medium" data-testid="check-fail-headline">
        {humanizeNodeRefs(headline, draft)}
      </p>
      {detail && (
        <p
          className="whitespace-pre-wrap break-words text-xs text-muted-foreground"
          data-testid="check-fail-detail"
        >
          {humanizeNodeRefs(detail, draft)}
        </p>
      )}
      {hint && (
        <p
          className="text-xs text-foreground/80"
          data-testid="check-fail-hint"
        >
          What to fix: {hint}
        </p>
      )}
    </div>
  );
}
