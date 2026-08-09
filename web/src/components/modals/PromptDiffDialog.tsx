/**
 * PromptNode prompt_template VERSION DIFF viewer (History modal).
 *
 * GitHub-style side-by-side diff of a single PromptNode's `prompt_template`:
 *   - RIGHT column  = the node's CURRENT prompt_template (the live value the
 *     editor holds; passed in as `currentPrompt`).
 *   - LEFT column   = the same node's prompt_template at a HISTORICAL workflow
 *     version. Above it we show that version's `v{major}.sv{sub}` label plus
 *     `<` / `>` buttons that step the LEFT side OLDER / NEWER.
 *
 * Skip-identical: consecutive workflow versions whose prompt for THIS node is
 * unchanged are skipped — `<` jumps to the next OLDER version whose prompt
 * actually differs, `>` to the next NEWER one (see `stepToDifferingVersion`).
 * `<` disables when there's no older differing prompt, `>` when there's no
 * newer one.
 *
 * Data sources (all pre-existing, no backend change):
 *   - `useWorkflowVersions(wfId)` → the full version list (major/sub).
 *   - `workflowAtQuery(wfId, v, sv)` via `queryClient.fetchQuery` → each
 *     version's immutable snapshot, fetched LAZILY as the user steps and
 *     cached under the SAME `['workflow-at', …]` key the canvas pin uses.
 *
 * Diff rendering uses `react-diff-viewer-continued` in `splitView` mode
 * (left=old, right=new, line add/remove highlighting built in), which matches
 * the GitHub side-by-side look and officially supports React 19.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import ReactDiffViewer, { DiffMethod } from 'react-diff-viewer-continued';
import { useTranslation } from 'react-i18next';
import { ChevronLeft, ChevronRight } from 'lucide-react';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { useWorkflowVersions, workflowAtQuery } from '@/lib/api/queries/workflow';
import {
  configFieldAtVersion,
  sortVersionsNewestFirst,
  stepToDifferingVersion,
  versionLabel,
  type StepDirection,
  type WorkflowVersionRef,
} from '@/pages/canvas/inspector/config-editors/prompt-history';

export interface PromptDiffDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  wfId: string;
  nodeId: string;
  /** The live current value of the diffed field (RIGHT column). */
  currentPrompt: string;
  /**
   * Which `node_config` text field to diff across versions. Defaults to
   * `prompt_template` (PromptNode); TemplateNode passes `template`. Kept
   * optional so existing PromptNode call sites stay unchanged.
   */
  field?: string;
  /** Optional dialog title override (i18n string). */
  title?: string;
  /** Optional dialog subtitle override (i18n string). */
  subtitle?: string;
}

/** Narrow the untyped `/versions` payload to the coordinates we use. */
function readVersions(data: unknown): WorkflowVersionRef[] {
  const list =
    data && typeof data === 'object' && Array.isArray((data as { versions?: unknown }).versions)
      ? ((data as { versions: unknown[] }).versions)
      : [];
  const out: WorkflowVersionRef[] = [];
  for (const raw of list) {
    if (!raw || typeof raw !== 'object') continue;
    const r = raw as Record<string, unknown>;
    if (typeof r.major === 'number' && typeof r.sub === 'number') {
      out.push({
        major: r.major,
        sub: r.sub,
        version_str: typeof r.version_str === 'string' ? r.version_str : undefined,
      });
    }
  }
  return out;
}

export function PromptDiffDialog({
  open,
  onOpenChange,
  wfId,
  nodeId,
  currentPrompt,
  field = 'prompt_template',
  title,
  subtitle,
}: PromptDiffDialogProps) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();

  // Version list — fetched once the modal opens.
  const versionsQuery = useWorkflowVersions(open ? wfId : undefined);
  const versions = useMemo(
    () => sortVersionsNewestFirst(readVersions(versionsQuery.data)),
    [versionsQuery.data],
  );

  // Per-version prompt cache (string), keyed by "major.sub". Filled lazily as
  // we fetch snapshots; `getPromptAt` reads it synchronously for the pure
  // stepping helper, returning '' for not-yet-fetched versions (those get
  // hydrated before they're compared — see ensurePrompt below).
  const [promptCache, setPromptCache] = useState<Record<string, string>>({});
  // The index (into `versions`, newest→oldest) currently shown on the LEFT,
  // or null until we've resolved an initial differing version.
  const [leftIndex, setLeftIndex] = useState<number | null>(null);
  // In-flight guard for the initial-resolve effect. A REF (not state): the
  // resolve effect both reads it AND flips it, so making it reactive state put
  // it in the effect's own dep array — setting it true re-ran the effect,
  // whose cleanup set `cancelled=true` on the in-flight async loop BEFORE it
  // could call setLeftIndex. The loop then bailed and `resolving` never reset
  // → leftIndex stayed null → the dialog hung on "Loading…" forever. A ref
  // doesn't trigger a re-render, so the async resolve completes uninterrupted.
  const resolvingRef = useRef(false);
  // True when NO historical snapshot could be loaded (every per-version fetch
  // failed) — so we show an error instead of an infinite "Loading…".
  const [resolveError, setResolveError] = useState(false);

  // Fetch + cache the prompt for version index `i` (shares the canvas pin's
  // immutable React Query entry). Returns the prompt string.
  const ensurePrompt = useCallback(
    async (i: number): Promise<string> => {
      const key = `${versions[i].major}.${versions[i].sub}`;
      const snap = await queryClient.fetchQuery({
        ...workflowAtQuery(wfId, versions[i].major, versions[i].sub),
        staleTime: Infinity, // historical pins are immutable
        retry: 1, // fail fast — don't hang "Loading…" through 3 retries
      });
      const prompt = configFieldAtVersion(
        (snap as { workflow?: Record<string, unknown> })?.workflow,
        nodeId,
        field,
      );
      setPromptCache((prev) =>
        prev[key] === prompt ? prev : { ...prev, [key]: prompt },
      );
      return prompt;
    },
    [queryClient, wfId, nodeId, versions, field],
  );

  // Resolve the INITIAL left version when the modal opens: the newest version
  // whose prompt for this node DIFFERS from the current value. We walk from
  // newest → oldest, fetching lazily, and stop at the first difference.
  useEffect(() => {
    if (!open || versions.length === 0 || leftIndex !== null || resolvingRef.current)
      return;
    let cancelled = false;
    resolvingRef.current = true;
    setResolveError(false);
    (async () => {
      try {
        let found: number | null = null;
        let anyLoaded = false;
        for (let i = 0; i < versions.length; i++) {
          let p: string;
          try {
            p = await ensurePrompt(i);
          } catch {
            continue; // one version's snapshot failed to load → skip it
          }
          if (cancelled) return;
          anyLoaded = true;
          if (p !== currentPrompt) {
            found = i;
            break;
          }
        }
        if (cancelled) return;
        if (!anyLoaded) {
          // Nothing loadable → surface an error instead of an infinite
          // "Loading…" (the old loop threw on the first fetch failure and left
          // leftIndex null forever).
          setResolveError(true);
        } else {
          // Every loaded version equals current → fall back to the OLDEST so we
          // still show two (identical) panes.
          setLeftIndex(found ?? versions.length - 1);
        }
      } finally {
        resolvingRef.current = false;
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [open, versions, leftIndex, ensurePrompt, currentPrompt]);

  // Reset state each time the modal closes so a re-open re-resolves cleanly
  // (and a different node / workflow never shows stale prompts).
  useEffect(() => {
    if (!open) queueMicrotask(() => {
      setLeftIndex(null);
      setPromptCache({});
      resolvingRef.current = false;
      setResolveError(false);
    });
  }, [open]);

  const getPromptAt = useCallback(
    (i: number) => {
      // While the modal CLOSES, `useWorkflowVersions(undefined)` disables and
      // `versions` collapses to [] before the reset effect nulls `leftIndex` —
      // so a stale index must never read `.major` off an undefined version.
      const v = versions[i];
      if (!v) return '';
      return promptCache[`${v.major}.${v.sub}`] ?? '';
    },
    [promptCache, versions],
  );

  // A `leftIndex` that's still set from a prior render but now out of range for
  // the current `versions` (the closing-collapse window above) is treated as
  // unresolved, so no render-time access dereferences an undefined version.
  const safeLeftIndex =
    leftIndex !== null && leftIndex >= 0 && leftIndex < versions.length ? leftIndex : null;

  // Can we step further in each direction? Computed against the CACHE; since
  // we hydrate the whole chain lazily, we precompute prompts for all versions
  // once an initial index is set so the enable/disable + skip logic is exact.
  const [hydrated, setHydrated] = useState(false);
  useEffect(() => {
    if (!open || leftIndex === null || hydrated) return;
    let cancelled = false;
    (async () => {
      for (let i = 0; i < versions.length; i++) {
        if (cancelled) return;
        await ensurePrompt(i);
      }
      if (!cancelled) setHydrated(true);
    })();
    return () => {
      cancelled = true;
    };
  }, [open, leftIndex, hydrated, versions, ensurePrompt]);
  useEffect(() => {
    if (!open) queueMicrotask(() => setHydrated(false));
  }, [open]);

  const step = (direction: StepDirection) => {
    if (leftIndex === null) return;
    const next = stepToDifferingVersion(versions, leftIndex, direction, getPromptAt);
    if (next !== null) setLeftIndex(next);
  };

  const olderDisabled =
    safeLeftIndex === null ||
    !hydrated ||
    stepToDifferingVersion(versions, safeLeftIndex, 'older', getPromptAt) === null;
  const newerDisabled =
    safeLeftIndex === null ||
    !hydrated ||
    stepToDifferingVersion(versions, safeLeftIndex, 'newer', getPromptAt) === null;

  const leftPrompt = safeLeftIndex !== null ? getPromptAt(safeLeftIndex) : '';
  const leftLabel = safeLeftIndex !== null ? versionLabel(versions[safeLeftIndex]) : '';

  const loading =
    versionsQuery.isLoading || (leftIndex === null && !resolveError);
  const fewVersions = versionsQuery.isSuccess && versions.length < 2;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-5xl">
        <DialogHeader>
          <DialogTitle>
            {title ?? t('prompt_history.title', 'Prompt history')}
          </DialogTitle>
          <DialogDescription>
            {subtitle ??
              t(
                'prompt_history.subtitle',
                'A historical version on the left, the current prompt on the right.',
              )}
          </DialogDescription>
        </DialogHeader>

        {versionsQuery.isError || resolveError ? (
          <p className="text-sm text-destructive">
            {t('prompt_history.load_error', 'Failed to load version history.')}
          </p>
        ) : fewVersions ? (
          <p className="py-8 text-center text-sm text-muted-foreground">
            {t('prompt_history.none', 'No earlier versions to compare.')}
          </p>
        ) : (
          <div className="min-w-0 space-y-2" data-role="prompt-diff">
            {/* Column headers: LEFT version stepper + RIGHT "current" label */}
            <div className="grid grid-cols-2 gap-3 text-xs">
              <div className="flex items-center gap-2">
                <Button
                  type="button"
                  size="icon"
                  variant="outline"
                  className="h-6 w-6"
                  disabled={olderDisabled}
                  onClick={() => step('older')}
                  aria-label={t('prompt_history.older', 'Older version')}
                  data-role="prompt-diff-older"
                >
                  <ChevronLeft className="h-4 w-4" />
                </Button>
                <span className="font-mono font-medium" data-role="prompt-diff-version">
                  {loading
                    ? t('prompt_history.loading', 'Loading…')
                    : t('prompt_history.version_label', {
                        version: leftLabel,
                        defaultValue: 'Version {{version}}',
                      })}
                </span>
                <Button
                  type="button"
                  size="icon"
                  variant="outline"
                  className="h-6 w-6"
                  disabled={newerDisabled}
                  onClick={() => step('newer')}
                  aria-label={t('prompt_history.newer', 'Newer version')}
                  data-role="prompt-diff-newer"
                >
                  <ChevronRight className="h-4 w-4" />
                </Button>
              </div>
              <div className="flex items-center font-medium text-muted-foreground">
                {t('prompt_history.current', 'Current')}
              </div>
            </div>

            <div className="max-h-[60vh] min-w-0 overflow-auto rounded-md border text-xs">
              {loading ? (
                <div className="grid grid-cols-2 gap-3 p-3">
                  <Skeleton className="h-72" />
                  <Skeleton className="h-72" />
                </div>
              ) : (
                <ReactDiffViewer
                  oldValue={leftPrompt}
                  newValue={currentPrompt}
                  splitView
                  compareMethod={DiffMethod.WORDS}
                  hideLineNumbers={false}
                  showDiffOnly={false}
                  // Constrain the diff <table> to the dialog width and wrap long
                  // prompt lines, so the RIGHT (current) column can't overflow
                  // past the modal's edge (it has no horizontal scroll bound).
                  styles={{
                    diffContainer: { tableLayout: 'fixed', width: '100%' },
                    content: { width: 'auto' },
                    contentText: {
                      whiteSpace: 'pre-wrap',
                      wordBreak: 'break-word',
                      overflowWrap: 'anywhere',
                    },
                  }}
                />
              )}
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
