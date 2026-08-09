/**
 * Pure helpers for the PromptNode prompt_template version-diff viewer.
 *
 * The History modal shows a GitHub-style side-by-side diff: the RIGHT column
 * is the node's CURRENT prompt_template, the LEFT column is the same node's
 * prompt_template at a HISTORICAL workflow version. The user steps the LEFT
 * side OLDER / NEWER with `<` / `>`, and consecutive versions that carry the
 * SAME prompt for this node are SKIPPED (we only ever land on a version whose
 * prompt actually differs from the one currently shown on the left).
 *
 * These helpers are deliberately network-free: `getPromptAt` is injected so
 * the stepping logic is unit-testable without React Query. The component wires
 * it to a memoized cache backed by `useWorkflowAt` snapshots.
 */

/** A workflow version as returned by `GET /api/v1/workflows/{wf_id}/versions`
 * (the route passes the repo's untyped superset through). We only need the
 * coordinates; everything else is optional / ignored. */
export interface WorkflowVersionRef {
  major: number;
  sub: number;
  version_str?: string;
  v?: number;
  sv?: number;
  ts?: number;
  timestamp?: number;
}

/**
 * Pull a node's text config FIELD out of a per-version workflow snapshot.
 *
 * `workflowAtVersion` is the flat `{ [node_id]: node }` dict (the `workflow`
 * field of `WorkflowSnapshotOut`). `field` is the `node_config` key to read
 * (e.g. `prompt_template` for PromptNode, `template` for TemplateNode).
 * Returns `''` when the workflow is missing, the node is absent at that
 * version, or the field isn't a string — so a caller can always treat the
 * result as a plain string for diffing.
 */
export function configFieldAtVersion(
  workflowAtVersion: Record<string, unknown> | null | undefined,
  nodeId: string,
  field: string,
): string {
  if (!workflowAtVersion || typeof workflowAtVersion !== 'object') return '';
  const node = (workflowAtVersion as Record<string, unknown>)[nodeId];
  if (!node || typeof node !== 'object') return '';
  const config = (node as Record<string, unknown>).node_config;
  if (!config || typeof config !== 'object') return '';
  const val = (config as Record<string, unknown>)[field];
  return typeof val === 'string' ? val : '';
}

/**
 * Back-compat alias: pull this node's `prompt_template`. Thin wrapper over
 * {@link configFieldAtVersion} kept so existing PromptNode call sites and
 * tests don't churn.
 */
export function promptAtVersion(
  workflowAtVersion: Record<string, unknown> | null | undefined,
  nodeId: string,
): string {
  return configFieldAtVersion(workflowAtVersion, nodeId, 'prompt_template');
}

export type StepDirection = 'older' | 'newer';

/**
 * Build a canonical `v{major}.sv{sub}` label for a version (the Explorer
 * convention — note the repo's own `version_str` is `v{major}.{sub}` WITHOUT
 * the `sv`, so we format our own to stay consistent across the app).
 */
export function versionLabel(v: WorkflowVersionRef): string {
  return `v${v.major}.sv${v.sub}`;
}

/**
 * Sort a version list NEWEST → OLDEST (highest major, then highest sub).
 * Returns a new array; the input is untouched.
 */
export function sortVersionsNewestFirst(
  versions: readonly WorkflowVersionRef[],
): WorkflowVersionRef[] {
  return [...versions].sort((a, b) => b.major - a.major || b.sub - a.sub);
}

/**
 * Step the LEFT-column version to the next one whose prompt DIFFERS from the
 * version currently shown on the left, skipping identical ones.
 *
 * - `versions` MUST be ordered NEWEST → OLDEST (index 0 = newest).
 * - `currentIndex` is the index currently displayed on the left.
 * - `direction === 'older'` walks toward higher indices (older versions);
 *   `'newer'` walks toward lower indices (newer versions).
 * - `getPromptAt(index)` returns this node's prompt at that version (inject a
 *   cache-backed fn; identical-prompt versions are detected by string equality).
 *
 * Returns the index of the next version whose prompt differs from the current
 * left prompt, or `null` if there is none in that direction (→ disable the
 * corresponding `<` / `>` button).
 */
export function stepToDifferingVersion(
  versions: readonly WorkflowVersionRef[],
  currentIndex: number,
  direction: StepDirection,
  getPromptAt: (index: number) => string,
): number | null {
  if (currentIndex < 0 || currentIndex >= versions.length) return null;
  const currentPrompt = getPromptAt(currentIndex);
  const stride = direction === 'older' ? 1 : -1;
  for (
    let i = currentIndex + stride;
    i >= 0 && i < versions.length;
    i += stride
  ) {
    if (getPromptAt(i) !== currentPrompt) return i;
  }
  return null;
}
