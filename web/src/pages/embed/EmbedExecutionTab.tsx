/**
 * Embedded side panel execution tab.
 *
 * REUSES the main app's `WorkflowRunTab` verbatim (input form → Execute → live
 * per-node status/results, reload-safe). That component is cleanly reusable —
 * it needs only a `wfId` and reads everything else from the shared module
 * stores (`useExecStreamStore`, `useWorkflowEditStore`).
 *
 * The ONE coupling worked around here: `WorkflowRunTab` reads the START-node
 * input fields and the save-if-dirty guard from `useWorkflowEditStore.draft`,
 * which the embed (unlike the canvas route) never hydrates — the embed only
 * seeds `lastActiveWorkflowId`, not the draft. So this thin wrapper hydrates
 * the draft from the same `useWorkflow(wfId)` snapshot the canvas page uses
 * (`setDraft`), then renders `WorkflowRunTab`. With the draft in place the run
 * input form materializes and save-before-run behaves exactly as on canvas.
 *
 * Hydration policy: seed the draft ONLY when it is empty or belongs to a
 * different workflow, and never when it is dirty — so re-mounting the tab (or
 * tab-switching) cannot clobber unsaved edits a user made elsewhere in this
 * same partition. The main-app canvas owns the rich agent↔edit reconcile; the
 * embed only needs a clean read-mostly seed to drive a run.
 */
import { useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { useWorkflow } from '@/lib/api/queries/workflow';
import { useWorkflowEditStore } from '@/stores/workflow-edit';
import { WorkflowRunTab } from '@/pages/canvas/inspector/WorkflowRunTab';
import type { components } from '@/lib/api/schema';

type WorkflowDraft = components['schemas']['CommitRequest']['workflow'];

export interface EmbedExecutionTabProps {
  wfId: string;
}

export function EmbedExecutionTab({ wfId }: EmbedExecutionTabProps) {
  const { t } = useTranslation();
  const query = useWorkflow(wfId);
  const draft = useWorkflowEditStore((s) => s.draft);
  const dirty = useWorkflowEditStore((s) => s.dirty);
  const setDraft = useWorkflowEditStore((s) => s.setDraft);

  // Hydrate the edit-store draft from the server snapshot so the reused
  // `WorkflowRunTab` can read the start-node input fields + run the saved
  // version. Guarded: only seed an EMPTY or wrong-workflow draft, never a dirty
  // one (don't clobber unsaved edits). The server snapshot's `workflow` field
  // is the flat `{node_id: …, __meta__}` dict the draft expects.
  const serverWorkflow = query.data?.workflow as WorkflowDraft | undefined;
  const draftWfId =
    draft && typeof draft === 'object'
      ? ((draft as Record<string, unknown>).__meta__ as
          | { workflow_id?: string }
          | undefined
        )?.workflow_id
      : undefined;
  const draftMatches = draftWfId === wfId;
  useEffect(() => {
    if (!serverWorkflow) return;
    if (dirty && draftMatches) return; // keep unsaved edits for THIS workflow
    if (draft && draftMatches) return; // already hydrated for this workflow
    setDraft(serverWorkflow);
    // Re-run when the snapshot arrives or the bound workflow changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [serverWorkflow, wfId]);

  if (query.isLoading || !draft) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
        {t('inspector.run.loading', 'Loading execution…')}
      </div>
    );
  }

  if (query.isError) {
    return (
      <div className="flex h-full items-center justify-center px-4 text-center text-sm text-destructive">
        {t('embed.execution.loadFailed', 'Could not load this workflow.')}
      </div>
    );
  }

  return (
    <div className="h-full overflow-y-auto p-4">
      <WorkflowRunTab wfId={wfId} />
    </div>
  );
}
