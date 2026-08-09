import { referenceCandidatesFromAncestors } from '@/lib/workflow/graph';
import type { WorkflowDraft } from '@/stores/workflow-edit';

export function computeReferenceCandidates(draft: WorkflowDraft | null, selfId: string): string[] {
  return referenceCandidatesFromAncestors(draft, selfId);
}
